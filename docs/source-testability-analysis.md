# Source Code Testability Analysis

**Project:** IdeaFlow / LeastGen Labs (`/Users/khalid/Desktop-fast/` → `/Users/khalid/Desktop/think-fast/`)
**Date:** 2026-09-16
**Purpose:** Supplementary data for t6 (router deep-dive) and t7 (security audit)
**Scope:** Five backend modules (`auth.py`, `billing.py`, `pipeline.py`, `database.py`, `scoop.py`) + `frontend/app.js`

---

## 1. Backend Module Analysis

---

### 1.1 `backend/routers/auth.py` (225 lines)

#### Functions & Signatures

| Function | Signature | Type |
|----------|-----------|------|
| `create_jwt` | `(user_id: str) -> str` | Pure helper |
| `decode_jwt` | `(token: str) -> dict[str, Any] \| None` | Pure helper |
| `get_current_user` | `(request: Request, credentials: HTTPAuthorizationCredentials \| None = Depends(security)) -> dict[str, Any]` | Dependency |
| `signup` | `(req: SignupRequest, response: Response) -> AuthResponse` | Route POST /auth/signup |
| `login` | `(req: LoginRequest, response: Response) -> AuthResponse` | Route POST /auth/login |
| `logout` | `(response: Response) -> dict` | Route POST /auth/logout |
| `get_profile` | `(user: dict = Depends(get_current_user)) -> UserProfile` | Route GET /auth/me |
| `list_my_runs` | `(user: dict = Depends(get_current_user)) -> list[RunRecord]` | Route GET /auth/runs |
| `check_limits` | `(user: dict = Depends(get_current_user)) -> dict` | Route GET /auth/limits |

#### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `backend.database` | DB | `authenticate_user`, `create_user`, `get_user_by_id`, `get_user_runs`, `check_run_limit`, `increment_run_count` |
| `jwt` (PyJWT) | Crypto | HMAC-SHA256 signing/verification |
| `os.environ` | Env | `OPENRESEARCH_JWT_SECRET` (falls back to `secrets.token_hex(32)`) |
| `Request.cookies` | HTTP | Reads `session` cookie |
| `Response.set_cookie` / `delete_cookie` | HTTP | Sets/clears HTTP-only cookie |

#### Key Logic & Edge Cases

1. **`create_jwt`** — Deterministic given `user_id` + `JWT_SECRET`. 72-hour expiry. **Testable in isolation** if `JWT_SECRET` is fixed.

2. **`decode_jwt`** — Catches all `jwt.PyJWTError` and returns `None`. Edge cases:
   - Expired token → returns `None` (correctly)
   - Tampered token → returns `None`
   - Malformed token → returns `None`
   - Token signed with wrong secret → returns `None`

3. **`get_current_user`** — Multi-source token extraction:
   - Cookie `session` first, then `Authorization: Bearer` header
   - No token → 401
   - Invalid token → 401
   - User deleted after token issued → 401
   - **Testability concern:** depends on `Request` object + `get_user_by_id`. Needs `TestClient` or mock `Request`.

4. **`signup`** — Two-layer duplicate check:
   - Calls `authenticate_user(email, password)` first (checking if any user with that email+password exists — this is a **logic oddity**: it checks password too, not just email)
   - Then `create_user` which also catches `IntegrityError` on unique email
   - Sets `secure=False` cookie explicitly (comment: "Set True in production with HTTPS")
   - **Edge case:** If `authenticate_user` returns a user but `create_user` also fails — both return 409.

5. **`login`** — Single `authenticate_user` check. Returns 401 on failure. No rate limiting.

6. **`get_profile`** — Depends on `get_current_user`. Maps DB dict to `UserProfile` model. All fields are pass-through.

7. **`list_my_runs`** — Depends on `get_current_user`. Maps DB rows to `RunRecord` models. `completed_at` is optional.

8. **`check_limits`** — Returns `can_run`, `runs_used`, `runs_limit`, `remaining`. Simple arithmetic.

#### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Isolated function tests** | Good | `create_jwt`, `decode_jwt` are pure and testable with fixed `JWT_SECRET` |
| **Route tests (unit)** | Moderate | Need `TestClient` + mocked `backend.database` functions. Cookie setting/reading needs `TestClient` round-trips |
| **Dependency injection** | Good | `get_current_user` uses `Depends(security)` — mockable via FastAPI dependency overrides |
| **DB coupling** | Tight | All routes call real DB functions. No repository pattern. Need to mock `backend.database.*` or use test SQLite |
| **Env dependency** | Moderate | `JWT_SECRET` from env with random fallback — must be fixed in test env |

#### Recommended Test Coverage

- `create_jwt`/`decode_jwt`: valid token round-trip, expired token, tampered token, wrong secret
- `get_current_user`: no token, invalid token, expired token, valid token, user deleted after token issue
- `signup`: success, duplicate email, missing fields (Pydantic validation)
- `login`: success, wrong password, nonexistent email
- `logout`: cookie cleared
- `get_profile`: field mapping correctness
- `list_my_runs`: empty list, populated list, `completed_at` None handling
- `check_limits`: can run, at limit, over limit

---

### 1.2 `backend/routers/billing.py` (363 lines)

#### Functions & Signatures

| Function | Signature | Type |
|----------|-----------|------|
| `_call_tap` | `(method: str, path: str, body: dict \| None = None) -> dict[str, Any]` | Internal HTTP helper |
| `_verify_tap_signature` | `(raw_body: bytes, signature: str) -> bool` | Internal crypto helper |
| `list_plans` | `() -> dict` | Route GET /billing/plans |
| `create_checkout` | `(req: CreateCheckoutRequest, user: dict = Depends(get_current_user)) -> dict` | Route POST /billing/create-checkout |
| `tap_webhook` | `(request: Request) -> dict` | Route POST /billing/webhook |
| `verify_charge` | `(charge_id: str) -> dict` | Route GET /billing/verify/{charge_id} |
| `get_my_subscription` | `(user: dict = Depends(get_current_user)) -> dict` | Route GET /billing/subscription |

#### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `backend.database` | DB | `create_subscription`, `get_user_subscription`, `update_user` |
| `backend.routers.auth.get_current_user` | Auth | Dependency for user-scoped routes |
| `urllib.request` | HTTP | Calls Tap API (`api.tap.company/v2`) |
| `hmac` + `hashlib` | Crypto | HMAC-SHA256 webhook signature verification |
| `os.environ` | Env | `TAP_API_KEY`, `TAP_MERCHANT_ID`, `TAP_WEBHOOK_SECRET`, `TAP_POST_URL` |
| `json` | Data | Request/response serialization |

#### Key Logic & Edge Cases

1. **`_call_tap`** — Raw HTTP client to Tap API:
   - Returns 501 if `TAP_API_KEY` is empty
   - 30-second timeout
   - HTTP errors → 502 with truncated body (500 chars)
   - URL errors → 502 with reason
   - **Testability:** Needs `unittest.mock` to patch `urllib.request.urlopen`. The 500-char truncation of error body is a subtle detail.

2. **`_verify_tap_signature`** — HMAC-SHA256 comparison:
   - Returns `False` if `TAP_WEBHOOK_SECRET` is empty (not an error — just skips verification)
   - Uses `hmac.compare_digest` (timing-safe)
   - **Testable in isolation** with known key/body/signature triples

3. **`list_plans`** — Returns static `PLANS` list. Trivial. **Fully testable.**

4. **`create_checkout`** — Complex flow:
   - Looks up plan in `PLAN_MAP` → 400 if unknown
   - Free plan (price ≤ 0) → activates immediately via `update_user`, returns success URL
   - Paid plan → builds charge payload with customer info, calls `_call_tap("POST", "charges", ...)`, extracts transaction URL
   - Stores `charge_id` via `update_user(user["id"], stripe_customer_id=charge_id)` — **field name mismatch**: DB column is `stripe_customer_id` but this is a Tap (not Stripe)integration
   - Default `success_url`/`cancel_url` point to `http://localhost:8756` (hardcoded dev URL)
   - **Edge cases:**
     - `TAP_MERCHANT_ID` empty → `merchant` field is `{}`
     - `_call_tap` fails → 502
     - No `transaction.url` or `redirect.url` in response → 502
     - `charge_id` missing from response → stored as empty string

5. **`tap_webhook`** — Webhook handler:
   - Reads raw body, checks `X-Tap-Signature` or `X-Signature` header
   - Skips signature verification if `TAP_WEBHOOK_SECRET` is empty (security concern — see t7)
   - Parses JSON, extracts `charge_id`, `status`, `metadata`
   - On `status == "CAPTURED" && response_code == "000"` → activates subscription
   - Extracts `user_id` from `udf2` metadata (`"user:<id>"`) and `plan_id` from `udf1` (`"plan:<id>"`)
   - **Edge cases:**
     - Invalid JSON → 400
     - Missing/invalid signature with secret configured → 401
     - `CAPTURED` but `duration_days == 0` (free plan) → subscription NOT created via webhook (free plan activated at checkout time instead)
     - `user_id` or `plan_id` missing from metadata → silently ignores (no error)
     - `PLAN_MAP` lookup fails → silently ignores

6. **`verify_charge`** — Manual charge verification:
   - Calls `_call_tap("GET", f"charges/{charge_id}")`
   - Same activation logic as webhook on success
   - Returns detailed status including `success` boolean
   - **Edge case:** Charge not found by Tap → `_call_tap` raises HTTPError → 502

7. **`get_my_subscription`** — Returns user's subscription or default free plan. Falls back to free plan if no subscription record exists.

#### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Isolated function tests** | Good | `_verify_tap_signature` is pure crypto. `list_plans` is static |
| **Route tests (unit)** | Hard | `_call_tap` requires mocking `urllib.request`. Webhook needs raw request body + header manipulation |
| **DB coupling** | Tight | `update_user`, `create_subscription`, `get_user_subscription` all called directly |
| **Env dependency** | High | 4 env vars required for full functionality. Tests need to set these |
| **External API** | High | Tap API calls in `_call_tap` — must be fully mocked. No test sandbox mentioned |
| **HTTP client** | Raw | Uses `urllib.request` (not `httpx`/`requests`). Harder to mock than a session-based client |

#### Recommended Test Coverage

- `_verify_tap_signature`: valid signature, invalid signature, wrong key, empty secret (returns False), timing-attack resistance (compare_digest usage)
- `_call_tap`: success response, HTTP error (non-2xx), URL error, missing API key (501), timeout behavior
- `list_plans`: returns 3 plans with correct fields
- `create_checkout`: free plan activation, paid plan checkout URL returned, unknown plan (400), missing API key (501), Tap API error (502), missing transaction URL (502)
- `tap_webhook`: valid CAPTURED webhook activates subscription, invalid signature (401), invalid JSON (400), missing metadata fields (silent ignore), non-CAPTURED status (no-op)
- `verify_charge`: successful verification, failed charge, charge not found (502)
- `get_my_subscription`: with subscription, without subscription (free fallback)

---

### 1.3 `backend/routers/pipeline.py` (196 lines)

#### Functions & Signatures

| Function | Signature | Type |
|----------|-----------|------|
| `start_pipeline` | `(req: PipelineStartRequest) -> PipelineStartResponse` | Route POST /pipeline/start |
| `list_pipeline_runs` | `() -> list[RunStatusResponse]` | Route GET /pipeline/runs |
| `get_run_status` | `(run_id: str) -> RunStatusResponse` | Route GET /pipeline/runs/{run_id} |
| `trigger_phase0_fulltext` | `(run_id: str) -> dict` | Route POST /pipeline/runs/{run_id}/phase0-fulltext |
| `trigger_collision` | `(run_id: str) -> dict` | Route POST /pipeline/runs/{run_id}/collision |
| `trigger_skeleton` | `(run_id: str) -> dict` | Route POST /pipeline/runs/{run_id}/skeleton |
| `_scan_phases` | `(run_id: str) -> dict` | Internal helper |

#### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `subprocess.Popen` / `subprocess.run` | Process | Launches `run_pipeline.sh` and individual phase scripts |
| `Path` / filesystem | FS | Reads `ideaspark_run/` directory, phase output files |
| `uuid` | Utility | Not used in pipeline.py (used in scoop.py) |

#### Key Logic & Edge Cases

1. **`start_pipeline`** — Fire-and-forget:
   - Launches `run_pipeline.sh phase0 <query>` via `subprocess.Popen` with `stdout=DEVNULL, stderr=DEVNULL`
   - Returns immediately with `run_id="pending"` and status `"started"`
   - **Testability concern:** `run_id` is always `"pending"` — not a real ID. The actual run directory is created by the shell script. This makes it impossible to track the run from the API response.
   - **No input validation** on `query` (empty string accepted)
   - **No auth required** — anyone can start a pipeline

2. **`list_pipeline_runs`** — Directory scanner:
   - Scans `ideaspark_run/` for directories containing `query.txt`
   - Calls `_scan_phases` for each
   - Returns runs sorted by directory name (reverse)
   - **Edge case:** Empty directory → empty list. Missing `query.txt` → skipped.

3. **`get_run_status`** — Single run inspector:
   - 404 if run directory doesn't exist
   - Reads `query.txt`, calls `_scan_phases`, checks for `idea.std.en.md`
   - **Edge case:** `query.txt` missing → empty query string

4. **`trigger_phase0_fulltext`** — Sync subprocess:
   - 404 if run dir missing
   - Runs `run_pipeline.sh phase0-fulltext <run_id>` via `subprocess.run` with 600s timeout
   - Returns `success` boolean + last 500 chars of stdout
   - **Edge case:** Timeout → `subprocess.TimeoutExpired` propagates (FastAPI returns 504 by default? No — it's not caught, so it becomes a 500 with traceback)

5. **`trigger_collision`** — Same pattern as `trigger_phase0_fulltext` with 600s timeout.

6. **`trigger_skeleton`** — Same pattern with 60s timeout.

7. **`_scan_phases`** — Phase file scanner:
   - Iterates `PHASE_FILES` dict (15 phase keys → filenames)
   - Maps each phase key to a directory name via a long if/elif chain
   - Checks if the file exists → `{"status": "complete", "size": N}` or `{"status": "pending"}`
   - **Edge case:** The if/elif chain has a subtle bug potential: `phase2_coherence` and `phase2_refined` both map to `phase2_coherence` directory, but `phase2_refined` looks for `refined_candidate.json` in that dir. If `phase2_coherence_output.json` exists but `refined_candidate.json` doesn't, they report different statuses.
   - **Testability:** Pure filesystem logic. Testable with temp directories.

#### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Isolated function tests** | Good | `_scan_phases` is pure FS logic. Testable with temp dirs |
| **Route tests (unit)** | Hard | `start_pipeline` uses `subprocess.Popen` — need to mock. Other triggers use `subprocess.run` — mockable |
| **Filesystem coupling** | High | All run status logic depends on `ideaspark_run/` directory structure |
| **Process coupling** | High | Three routes launch subprocesses. Need to mock `subprocess.run`/`Popen` |
| **Auth** | None | No authentication on any pipeline route |
| **Input validation** | Weak | `query` not validated. `run_id` not sanitized (path traversal risk — see t7) |

#### Recommended Test Coverage

- `_scan_phases`: all 15 phase keys with existing files, all with missing files, mixed state, directory structure edge cases
- `start_pipeline`: subprocess launched with correct args, returns pending status, empty query accepted
- `list_pipeline_runs`: empty dir, dir with runs, dir without query.txt skipped
- `get_run_status`: existing run, missing run (404), missing query.txt
- `trigger_phase0_fulltext`: success, failure (non-zero returncode), timeout, missing run (404)
- `trigger_collision`: same pattern
- `trigger_skeleton`: same pattern with shorter timeout

---

### 1.4 `backend/database.py` (304 lines)

#### Functions & Signatures

| Function | Signature | Type |
|----------|-----------|------|
| `_get_conn` | `() -> sqlite3.Connection` | Internal (thread-local) |
| `init_db` | `() -> None` | Schema init |
| `_hash_password` | `(password: str) -> tuple[str, str]` | Internal (scrypt) |
| `_verify_password` | `(password: str, stored_hash: str, salt: str) -> bool` | Internal (scrypt) |
| `create_user` | `(email: str, password: str, name: str = "") -> dict \| None` | User CRUD |
| `get_user_by_email` | `(email: str) -> dict \| None` | User CRUD |
| `get_user_by_id` | `(user_id: str) -> dict \| None` | User CRUD |
| `authenticate_user` | `(email: str, password: str) -> dict \| None` | Auth |
| `update_user` | `(user_id: str, **kwargs) -> dict \| None` | User CRUD |
| `increment_run_count` | `(user_id: str) -> dict \| None` | Usage tracking |
| `check_run_limit` | `(user_id: str) -> tuple[bool, int, int]` | Quota |
| `create_run` | `(user_id: str, run_type: str, query: str) -> dict` | Run tracking |
| `get_user_runs` | `(user_id: str, limit: int = 50) -> list[dict]` | Run tracking |
| `complete_run` | `(run_id: str, tokens_used: int = 0, cost: float = 0.0) -> None` | Run tracking |
| `create_api_token` | `(user_id: str, name: str = "default") -> tuple[str, dict]` | API tokens |
| `validate_api_token` | `(raw_token: str) -> dict \| None` | API tokens |
| `create_subscription` | `(user_id, stripe_subscription_id, stripe_price_id, tier, status) -> dict` | Subscriptions |
| `get_user_subscription` | `(user_id: str) -> dict \| None` | Subscriptions |

#### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `sqlite3` | DB | Single-file SQLite, thread-local connections |
| `hashlib.scrypt` | Crypto | Password hashing (n=16384, r=8, p=1, dklen=64) |
| `secrets.token_hex` | Crypto | User IDs, run IDs, API tokens |
| `threading.local` | Threading | Thread-local connection storage |
| `os.environ` | Env | None directly (DB path is derived from `__file__`) |
| `datetime` | Time | `created_at`, `updated_at` timestamps |

#### Key Logic & Edge Cases

1. **`_get_conn`** — Thread-local connection caching:
   - Creates connection on first call per thread
   - Sets `WAL` journal mode and `foreign_keys=ON`
   - **Testability:** Shared global state. Tests that import `database.py` share the same connection unless reset.

2. **`init_db`** — Idempotent schema creation:
   - Called automatically on import (line 304: `init_db()`)
   - Creates 4 tables: `users`, `runs`, `api_tokens`, `subscriptions`
   - Creates 3 indexes
   - **Testability concern:** Import-side effect. Any test that imports `database.py` triggers `init_db()`. Need to handle this in test setup (use temp DB path).

3. **`_hash_password` / `_verify_password`** — scrypt with fixed parameters:
   - Salt is 16 hex bytes (32 chars)
   - scrypt params: n=16384, r=8, p=1, dklen=64
   - **Testable in isolation** but slow (~100ms per hash). Tests should use fewer iterations or mock.

4. **`create_user`** — Inserts user with hashed password:
   - Generates 12-byte hex user ID
   - Lowercases and strips email
   - Catches `IntegrityError` → returns `None` (email already exists)
   - Returns full user dict via `get_user_by_id`
   - **Edge case:** Email with leading/trailing whitespace → stripped. Email case → lowercased.

5. **`get_user_by_email`** — Case-insensitive lookup (lowercases email).

6. **`get_user_by_id`** — Direct ID lookup.

7. **`authenticate_user`** — Two-step: lookup by email, then verify password.

8. **`update_user`** — Whitelist-based field update:
   - Only allows: `name`, `subscription_tier`, `subscription_status`, `runs_used`, `runs_limit`, `stripe_customer_id`
   - **Critical:** Builds SQL via f-string: `f"UPDATE users SET {sets} WHERE id = ?"` — the field names come from the `allowed` whitelist, so SQL injection is prevented by whitelist, not by parameterization
   - Sets `updated_at` automatically
   - Returns `None` if no allowed fields provided (just returns current user)
   - **Edge case:** If `user_id` doesn't exist, returns `None`

9. **`increment_run_count`** — Raw SQL increment: `runs_used = runs_used + 1`

10. **`check_run_limit`** — Returns `(can_run, used, limit)`. Returns `(False, 0, 0)` if user not found.

11. **`create_run`** — Inserts run with 8-byte hex ID. `run_type` must be `'scoop'` or `'idea'` (CHECK constraint in DB).

12. **`get_user_runs`** — Ordered by `created_at DESC`, default limit 50.

13. **`complete_run`** — Sets status, tokens, cost, completed_at.

14. **`create_api_token`** — Generates `or_<48 hex chars>` token, SHA-256 hashes it for storage.

15. **`validate_api_token`** — Hashes input, looks up hash, updates `last_used_at`.

16. **`create_subscription` / `get_user_subscription`** — Subscription CRUD.

#### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Isolated function tests** | Good | `_hash_password`/`_verify_password` are pure. `check_run_limit` logic is simple |
| **DB tests** | Moderate | Need SQLite test database. Global `DB_PATH` and import-side `init_db()` are obstacles |
| **Thread-local state** | Problematic | `_get_conn()` uses `threading.local()`. Test isolation requires resetting or using separate threads |
| **Import-side effects** | Problematic | `init_db()` runs on import. Tests need to override `DB_PATH` before import |
| **Crypto speed** | Slow | scrypt is deliberately slow. Tests should mock or use reduced parameters |
| **SQL injection** | Low risk | Field names are whitelisted. Values are parameterized. But the f-string SQL construction is a code smell |

#### Recommended Test Coverage

- `_hash_password`/`_verify_password`: same password → same hash? No (random salt). Different passwords → different hashes. Correct verification, incorrect password fails. Salt uniqueness.
- `create_user`: success, duplicate email (returns None), email normalization (lowercase, strip)
- `get_user_by_email`: found, not found, case insensitivity
- `get_user_by_id`: found, not found
- `authenticate_user`: correct credentials, wrong password, nonexistent user
- `update_user`: update single field, update multiple fields, unknown field ignored, no allowed fields (returns current user), nonexistent user (returns None), `updated_at` set
- `increment_run_count`: increments by 1, multiple increments
- `check_run_limit`: under limit, at limit, over limit, nonexistent user
- `create_run`: creates run with correct fields, run_type CHECK constraint (scoop/idea)
- `get_user_runs`: returns runs in reverse chronological order, limit works
- `complete_run`: sets status and timestamps
- `create_api_token`/`validate_api_token`: token creation, valid token lookup, invalid token (None), token hash is not reversible
- `create_subscription`/`get_user_subscription`: create, retrieve, None if none exists

---

### 1.5 `backend/routers/scoop.py` (134 lines)

#### Functions & Signatures

| Function | Signature | Type |
|----------|-----------|------|
| `get_api_key` | `() -> str` | Internal helper |
| `call_llm` | `(system: str, user: str, timeout: int = 120) -> str` | Internal HTTP helper |
| `start_scoop_check` | `(req: ScoopStartRequest) -> ScoopStatusResponse` | Route POST /scoop-check/start |
| `get_scoop_status` | `(scoop_id: str) -> ScoopStatusResponse \| JSONResponse` | Route GET /scoop-check/{scoop_id} |

#### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `subprocess.Popen` | Process | Launches `run_scoop.py <scoop_id>` |
| `urllib.request` | HTTP | Calls OpenRouter API (`openrouter.ai/api/v1/chat/completions`) |
| `os.environ` | Env | `OPENROUTER_API_KEY` |
| `/home/enigma/.kinox/env` | File | Fallback API key source |
| `uuid.uuid4` | Utility | Generates scoop_id |
| `json` | Data | Status files, LLM requests/responses |
| `Path` / filesystem | FS | `scoop_runs/<scoop_id>/` directory |

#### Key Logic & Edge Cases

1. **`get_api_key`** — Two-source key lookup:
   - First checks `OPENROUTER_API_KEY` env var
   - Falls back to reading `/home/enigma/.kinox/env` and parsing `OPENROUTER_API_KEY=` line
   - Returns empty string if neither source has a key
   - **Testability:** Hardcoded path `/home/enigma/.kinox/env` — not portable. Tests need to mock `os.environ` or patch `get_api_key`.

2. **`call_llm`** — Minimal OpenRouter client:
   - Builds JSON body with `deepseek/deepseek-v4-flash` model, temperature 0.2, max_tokens 4096
   - Sends via `urllib.request.urlopen` with Bearer auth
   - Returns `choices[0].message.content` or empty string
   - On any exception → returns `json.dumps({"error": str(e)})` as a string (not raised)
   - **Testability concern:** Error responses are strings, not exceptions. Caller must parse to detect errors. The `system` and `user` params are passed directly to the LLM — no input sanitization.
   - **Timeout:** 120s default, configurable.

3. **`start_scoop_check`** — Fire-and-forget scoop:
   - Generates 12-char hex scoop_id via `uuid.uuid4().hex[:12]`
   - Creates `scoop_runs/<scoop_id>/` directory
   - Writes `problem.txt`, `novelty.txt`, `status.json` (status: "started", step: 0)
   - Launches `run_scoop.py <scoop_id>` via `subprocess.Popen` with `DEVNULL` stdout/stderr
   - Returns `ScoopStatusResponse(scoop_id, status="started", step=0)`
   - **No auth required**
   - **No input validation** on `problem` or `novelty`

4. **`get_scoop_status`** — Status poll:
   - 404 if scoop directory doesn't exist (returns JSONResponse, not ScoopStatusResponse — **type inconsistency**)
   - If `status.json` doesn't exist → returns `ScoopStatusResponse(scoop_id, status="started", step=0)` (assumes started)
   - If status is "done" → loads `verdict.json` as result
   - **Edge case:** `status.json` exists but is malformed JSON → `json.loads` raises → 500

#### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Isolated function tests** | Moderate | `get_api_key` needs env/ mock. `call_llm` needs HTTP mock |
| **Route tests (unit)** | Hard | `start_scoop_check` uses `subprocess.Popen`. `get_scoop_status` reads filesystem |
| **Filesystem coupling** | High | All state is in `scoop_runs/` directories |
| **Process coupling** | High | `subprocess.Popen` for background launch |
| **HTTP coupling** | High | `call_llm` calls OpenRouter directly |
| **Auth** | None | No authentication on scoop routes |
| **Type inconsistency** | Bug | `get_scoop_status` returns `JSONResponse` for 404 but `ScoopStatusResponse` otherwise |

#### Recommended Test Coverage

- `get_api_key`: env var set, env var empty + kinox file present, both empty
- `call_llm`: successful response, API key missing (returns error JSON), network error (returns error JSON), timeout
- `start_scoop_check`: creates directory, writes input files, launches subprocess, returns correct response
- `get_scoop_status`: not found (404), status.json missing (assume started), status.json present with "started", status.json present with "done" + verdict.json, malformed status.json

---

## 2. Frontend Analysis: `frontend/app.js` (592 lines)

### Structure

```
App state:
  currentUser, authChecked, runs[], selectedRun, currentMode, pollTimer, scoopTimer

API layer (IIFE, ~27 lines):
  API.login, API.signup, API.me, API.logout, API.limits, API.runs,
  API.run, API.startPipeline, API.runPhase, API.scoopStart, API.scoopStatus,
  API.plans, API.createCheckout, API.subscription

Auth flow (~115 lines):
  checkAuth, handleAuthError, showAuth, showAuthForm, doLogin, doSignup, doLogout,
  toggleUserMenu, refreshSubscriptionStatus

Billing (~37 lines):
  showBilling, hideBilling, upgrade

Pipeline (~200 lines):
  PHASES[], PHASE_FILES{}, initApp, loadRuns, render, renderStats, renderRunTabs,
  renderStepper, renderPhases, renderHistory, renderIdeaCard, autoChain,
  startPolling, stopPolling, runPipeline, runScoop

Scoop (~80 lines):
  runScoop, updateScoopSteps, renderScoopResult

Export (~5 lines):
  exportCard

Misc (~15 lines):
  scrollToTop, scrollToForm, closeModal, openModal, handleCheckoutRedirect
```

### API Layer Analysis

The `API` object is an IIFE that creates a `base` URL from `window.location.pathname`:

```js
const base = window.location.pathname.match(/^\/d\/(\d+)\//)
  ? `${window.location.protocol}//${window.location.hostname}:${RegExp.$1}`
  : '';
```

- **Testability concern:** This regex-based base URL derivation is fragile. If the pathname doesn't match `/d/<digits>/`, `base` is empty string — all API calls go to relative URLs (same origin). This means the frontend works in two modes: dev mode (relative URLs) and embedded mode (`/d/8756/` → `http://host:8756`).

- The `h` helper wraps `fetch` with:
  - `credentials: 'include'` (cookies)
  - 401 → `handleAuthError()` + throw
  - Non-ok → parse JSON error or throw status text
  - JSON vs text response detection

- **Testability:** The `API` object is a standalone IIFE — easy to replace with a mock in tests. Each method returns a Promise.

### Key Functions & Testability

#### Auth Functions

| Function | What it does | Testability |
|----------|-------------|-------------|
| `checkAuth` | Calls `API.me()`, on success shows app UI, on failure shows landing | Needs DOM + API mock. toggles multiple DOM classes |
| `doLogin` | Prevents default, disables button, calls `API.login`, on success updates UI, on error shows message | Needs DOM + API mock. Button state management |
| `doSignup` | Same pattern as `doLogin` | Same |
| `doLogout` | Calls `API.logout`, silences errors, clears UI | Easy to test |
| `refreshSubscriptionStatus` | Calls `API.subscription()`, updates display element | Needs DOM element. Tier label mapping logic testable in isolation |
| `handleAuthError` | Clears state, hides app | Easy to test |

#### Pipeline Functions

| Function | What it does | Testability |
|----------|-------------|-------------|
| `initApp` | Calls `loadRuns()`, binds keyboard shortcuts (Ctrl+Enter, Enter in query field) | Needs DOM event simulation |
| `loadRuns` | Calls `API.runs()`, updates `runs[]`, calls `render()`, starts `autoChain` | Needs API mock |
| `render` | Orchestrates all render functions | Needs DOM |
| `renderStats` | Computes totals from `runs[]` and updates DOM | Pure computation + DOM. Stats computation is testable without DOM |
| `renderRunTabs` | Generates run tab buttons | Pure HTML generation from data — testable |
| `renderStepper` | Generates phase stepper UI | Pure HTML generation — testable |
| `renderPhases` | Generates phase list with status badges | Pure HTML generation — testable |
| `renderHistory` | Generates history list | Pure HTML generation — testable |
| `renderIdeaCard` | Fetches card markdown, renders with `marked` (if available) or `<pre>` fallback | Needs API mock + DOM. `marked` availability check is a branch |
| `runPipeline` | Validates query, calls `API.startPipeline`, starts polling | Needs DOM + API mock |
| `runScoop` | Validates problem+novelty, calls `API.scoopStart`, polls with `scoopTimer` | Needs DOM + API mock. Complex polling logic |
| `startPolling` / `stopPolling` | setInterval-based polling of `API.runs()` | Timer-based — needs fake timers in tests |

#### Billing Functions

| Function | What it does | Testability |
|----------|-------------|-------------|
| `showBilling` | Shows modal, calls `API.plans()`, renders plan cards | Needs DOM + API mock. HTML generation from plan data is testable |
| `upgrade` | Calls `API.createCheckout`, redirects to URL | Needs API mock |

#### Scoop Functions

| Function | What it does | Testability |
|----------|-------------|-------------|
| `updateScoopSteps` | Renders scoop progress steps | Pure HTML generation — testable |
| `renderScoopResult` | Renders verdict with level, axes, candidates, recommendation | Pure HTML generation from result data — testable |

### External Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `fetch` | HTTP | All API calls |
| `document.getElementById` | DOM | pervasive — ~30+ IDs referenced |
| `window.location` | URL | Base URL derivation, checkout redirect |
| `window.history.replaceState` | History | URL cleanup after checkout |
| `window.open` | Window | Export card opens in new tab |
| `window.scrollTo` / `scrollIntoView` | Scroll | UX |
| `alert` | Dialog | Used for checkout success/cancel, billing errors |
| `marked` (optional) | Markdown | `typeof marked !== 'undefined'` check |
| `renderMathInElement` (optional) | Math | KaTeX rendering, `typeof` check |
| `JSON.stringify` | Data | Modal content display |

### Key Logic & Edge Cases

1. **Auth state management:** `currentUser` and `authChecked` are module-level let bindings. No observable state pattern — state is implicit in module scope.

2. **Error handling pattern:** All async functions use `try/catch`. Errors are displayed via `err.message` in DOM elements or `alert()`. Network errors without `.message` fall back to generic strings.

3. **Button state management:** `doLogin`, `doSignup`, `runPipeline`, `runScoop` all disable buttons during async operations and re-enable in finally-like patterns. But error paths sometimes forget to re-enable (e.g., `runPipeline` re-enables in catch, `runScoop` re-enables in catch — but `doLogin` re-enables at end of function, outside try/catch, so it's safe).

4. **Polling:** `startPolling` polls `API.runs()` every 3 seconds. Stops when all runs have cards. No max retry count, no backoff.

5. **Scoop polling:** `runScoop` polls `API.scoopStatus()` every 2 seconds. Stops on "done" or "failed". No max retry count.

6. **DOM ID coupling:** The entire file is tightly coupled to specific DOM element IDs (e.g., `login-email`, `scoop-problem`, `idea-query`, `user-dropdown`, `billing-modal`, `stepper-bar`, `phases-list`, etc.). Any ID change breaks the JS.

7. **`renderIdeaCard` race condition:** Fetches run data and card markdown sequentially. If the run isn't found or card isn't ready, hides the card. No loading state.

8. **`handleCheckoutRedirect`:** Reads `?checkout=success` or `?checkout=canceled` from URL, shows alert, cleans URL. Called on `DOMContentLoaded`.

9. **Keyboard shortcuts:** Ctrl+Enter triggers scoop or pipeline based on `currentMode`. Enter in idea query field triggers pipeline.

10. **`exportCard`:** Opens `/api/export/<run_id>/<format>` in new tab. No validation that the run has a card.

### Testability Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Module structure** | Moderate | IIFE for API is good. Rest is loose functions with shared mutable state |
| **DOM coupling** | Very High | ~30+ hardcoded element IDs. No abstraction layer |
| **Network coupling** | High | All via `fetch`. Mockable via `fetch` override |
| **Timer coupling** | High | `setInterval` for polling — needs fake timer support |
| **Optional deps** | Good | `marked` and `renderMathInElement` are feature-detected |
| **State management** | Poor | Module-level lets with no encapsulation. Hard to reset between tests |
| **Pure functions** | Some | `renderStats` computation, `renderRunTabs`, `renderStepper`, `renderPhases`, `renderHistory`, `renderScoopResult` are mostly pure HTML generation from data |
| **Framework** | None | Vanilla JS. No component model. No test utilities |

### Recommended Test Coverage

**Unit tests (pure logic, no DOM):**
- `refreshSubscriptionStatus` tier label mapping: `pro_monthly` → "Pro Monthly", `pro_yearly` → "Pro Yearly", `free` → "Free", unknown → "Plan: Unknown"
- `renderStats` computation: total runs, completed (has_card), active (phase0 complete, no card), papers (phase0 size / 2000 rounded)
- `renderRunTabs` HTML generation from runs array
- `renderStepper` HTML generation from phases
- `renderPhases` HTML generation with status/ready logic
- `renderHistory` HTML generation
- `renderScoopResult` HTML generation from result object (all 5 levels, with/without candidates, with/without per_axis, with/without recommendation)
- `updateScoopSteps` HTML generation

**Integration tests (API mock + minimal DOM):**
- `checkAuth` success path: API.me returns user → app UI shown, user details displayed
- `checkAuth` failure path: API.me throws → landing shown
- `doLogin` success: button disabled during call, re-enabled after, user info displayed
- `doLogin` failure: error message displayed, button re-enabled
- `doSignup` success/failure: same pattern
- `doLogout`: clears user state, hides app
- `runPipeline` with empty query: border highlight, no API call
- `runPipeline` success: polling started, pipeline container shown
- `runPipeline` failure: button shows "Failed — try again", re-enables after 2s
- `runScoop` with missing problem/novelty: border highlights, no API call
- `runScoop` success: polling started, steps updated
- `runScoop` failure: poll stops, button re-enabled
- `showBilling` success: plans rendered as HTML cards
- `showBilling` failure: "Failed to load plans" shown
- `upgrade` free plan: alert "Already on free plan"
- `upgrade` paid plan: redirect to checkout URL
- `handleCheckoutRedirect` with success: alert + URL cleaned
- `handleCheckoutRedirect` with canceled: alert + URL cleaned
- `handleCheckoutRedirect` with no param: no-op
- `exportCard` with no run selected: no-op
- `startPolling`/`stopPolling`: interval created/cleared
- Keyboard shortcuts: Ctrl+Enter in scoop mode → `runScoop`, Ctrl+Enter in idea mode → `runPipeline`, Enter in query field → `runPipeline`

---

## 3. Cross-Cutting Testability Concerns

### 3.1 No Test Framework Anywhere

- No `pytest`, no `unittest`, no Jest, no Vitest
- `requirements.txt` has no test dependencies
- No `conftest.py`, no `setup.cfg`, no `pyproject.toml`
- The single existing test (`test_canvas_clamp.py`) uses raw `assert` + `__main__` block

### 3.2 Global State & Import Side Effects

| Module | Issue |
|--------|-------|
| `database.py` | `init_db()` called on import. `DB_PATH` is module-level constant. `_get_conn()` uses `threading.local()` |
| `auth.py` | `JWT_SECRET` computed on import from env or random |
| `billing.py` | `TAP_*` config from env on import |
| `pipeline.py` | `PROJECT_ROOT`, `RUN_DIR`, `PIPELINE_SCRIPT` computed on import from `__file__` |
| `scoop.py` | `PROJECT_ROOT`, `RUN_DIR`, `SCOOP_DIR`, `PIPELINE_SCRIPT`, `LLM_RUNNER`, `SKILL_DIR` computed on import |
| `phases.py` | Same pattern — `PROJECT_ROOT`, `SKILL_DIR`, `RUN_DIR` on import |
| `fulltext.py` | Same |
| `auto.py` | Same |
| `results.py` | Same |
| `export.py` | Same |
| `ui.py` | Same |
| `dashboard.py` | Same |

**Impact:** Testing any module in isolation requires careful env setup, path manipulation, or mocking of module-level constants. The `PROJECT_ROOT` derivation from `__file__` means tests running from a different directory get a different root.

### 3.3 Subprocess-Heavy Architecture

Multiple routers launch external processes:
- `pipeline.py`: `subprocess.Popen` (start_pipeline), `subprocess.run` (trigger_phase0_fulltext, trigger_collision, trigger_skeleton)
- `scoop.py`: `subprocess.Popen` (start_scoop_check)
- `phases.py`: `subprocess.run` (run_phase0, get_next_step)
- `fulltext.py`: `subprocess.run` (run_phase0_fulltext)
- `auto.py`: `subprocess.run` (trigger_auto_phase)

**Impact:** All of these need `unittest.mock.patch` on `subprocess.Popen`/`subprocess.run`. Timeout behavior needs testing. The `DEVNULL` stdout/stderr in fire-and-forget launches means no output capture for testing.

### 3.4 No Authentication on Most Routes

| Route | Auth? |
|-------|-------|
| POST /auth/signup | No |
| POST /auth/login | No |
| POST /auth/logout | No |
| GET /auth/me | Yes (JWT) |
| GET /auth/runs | Yes (JWT) |
| GET /auth/limits | Yes (JWT) |
| GET /billing/plans | No |
| POST /billing/create-checkout | Yes (JWT) |
| POST /billing/webhook | No (signature check only) |
| GET /billing/verify/{charge_id} | No |
| GET /billing/subscription | Yes (JWT) |
| POST /pipeline/start | **No** |
| GET /pipeline/runs | **No** |
| GET /pipeline/runs/{run_id} | **No** |
| POST /pipeline/runs/{run_id}/phase0-fulltext | **No** |
| POST /pipeline/runs/{run_id}/collision | **No** |
| POST /pipeline/runs/{run_id}/skeleton | **No** |
| POST /scoop-check/start | **No** |
| GET /scoop-check/{scoop_id} | **No** |

**Impact:** Unauthenticated routes need tests for: input validation, rate limiting (none exists), abuse scenarios.

### 3.5 Error Handling Inconsistencies

- `scoop.py:get_scoop_status` returns `JSONResponse` for 404 but `ScoopStatusResponse` otherwise (type mismatch in return type annotation)
- `billing.py:_call_tap` truncates error bodies to 500 chars silently
- `pipeline.py` trigger routes don't catch `subprocess.TimeoutExpired` explicitly — FastAPI may return 500 with traceback
- `auth.py:get_current_user` catches JWT errors but not DB errors from `get_user_by_id`
- `scoop.py:call_llm` returns error JSON as a string instead of raising — caller must parse to detect errors

### 3.6 Frontend-backend Contract

The frontend `API` object defines the implicit API contract. There's no OpenAPI schema validation on the frontend. The backend uses Pydantic models for request validation but the frontend sends raw JSON. Mismatches would result in 422 errors that the frontend displays as generic errors.

---

## 4. Summary: What a Comprehensive Test Suite Would Need

### Backend Test Infrastructure

1. **Test framework:** pytest (chosen for fixture support, parametrize, and FastAPI `TestClient` integration)
2. **Test client:** FastAPI `TestClient` (starlette test client) for route-level tests
3. **Database:** Temporary SQLite database per test session. Override `DB_PATH` before importing `database.py`. Reset schema between tests.
4. **Mocking strategy:**
   - `subprocess.Popen`/`subprocess.run` → `unittest.mock.AsyncMock` or `MagicMock`
   - `urllib.request.urlopen` → mock for Tap API and OpenRouter calls
   - `os.environ` → `monkeypatch` for env var tests
   - `backend.database.*` → mock or use real test DB
5. **Dependency overrides:** FastAPI `app.dependency_overrides` for `get_current_user`

### Test Categories

| Category | Priority | Modules |
|----------|----------|---------|
| Password hashing/verification | High | database.py |
| User CRUD (create, read, update) | High | database.py |
| Authentication (login, JWT create/decode) | High | auth.py, database.py |
| Auth dependency (get_current_user) | High | auth.py |
| Auth routes (signup, login, logout, profile, runs, limits) | High | auth.py |
| Plan listing | Medium | billing.py |
| Checkout creation (free + paid) | High | billing.py |
| Webhook signature verification | High | billing.py |
| Webhook processing | High | billing.py |
| Charge verification | Medium | billing.py |
| Subscription retrieval | Medium | billing.py |
| Pipeline start (subprocess launch) | High | pipeline.py |
| Pipeline run listing | Medium | pipeline.py |
| Pipeline run status | Medium | pipeline.py |
| Phase triggers (subprocess.run) | High | pipeline.py |
| Phase file scanning (_scan_phases) | High | pipeline.py |
| Scoop start (subprocess + filesystem) | High | scoop.py |
| Scoop status polling | Medium | scoop.py |
| LLM call (OpenRouter HTTP) | Medium | scoop.py |
| API key resolution | Low | scoop.py |

### Frontend Test Infrastructure

1. **Test framework:** Vitest or Jest (chosen for JS ecosystem, fake timers, fetch mock)
2. **DOM testing:** `jsdom` for DOM interaction tests
3. **Fetch mock:** `vitest-fetch-mock` or `jest-fetch-mock` or `msw`
4. **Fake timers:** `vi.useFakeTimers()` / `jest.useFakeTimers()` for polling tests

### Test Categories (Frontend)

| Category | Priority |
|----------|----------|
| Tier label mapping | High |
| Stats computation | High |
| HTML generation functions (run tabs, stepper, phases, history, scoop steps, scoop result) | High |
| Auth flow (login, signup, logout, checkAuth) | High |
| Pipeline flow (start, poll, display) | High |
| Scoop flow (start, poll, display result) | High |
| Billing flow (plans, checkout, upgrade) | Medium |
| Checkout redirect handler | Medium |
| Keyboard shortcuts | Low |
| Error handling (network errors, auth errors) | High |

---

*End of analysis. Saved to `/Users/khalid/Desktop-fast/docs/source-testability-analysis.md`.*
