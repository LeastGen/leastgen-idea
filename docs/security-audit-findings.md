# Security & Edge-Case Testing Audit — Findings Report

**Task:** t7 — Audit security and edge-case testing gaps  
**Auditor:** reviewer (qa-testing-audit team)  
**Date:** 2026-02-18  
**Scope:** Full-stack security testing assessment against `/Users/khalid/Desktop-fast/`  
**Framework:** `docs/testing-audit-framework.md` sections 6, 7f, 8  

---

## Executive Summary

**Zero security tests exist.** There are no `test_*.py` files under `backend/`, no frontend test files for `app.js`, and no test infrastructure of any kind. Every finding below is a **testing gap** — the underlying code has both strengths and weaknesses, but none of them are verified by tests.

**14 findings** identified: 3 Critical, 5 High, 4 Medium, 2 Low.

---

## Finding Summary Table

| # | Severity | Area | Finding | File (line) |
|---|---|---|---|---|
| F1 | **Critical** | Auth tests | No JWT forgery / tampered token rejection tests | `backend/routers/auth.py` (89-94) |
| F2 | **Critical** | Auth tests | No expired JWT rejection test | `backend/routers/auth.py` (84, 120-121) |
| F3 | **Critical** | Auth tests | No auth bypass tests (protected endpoints without credentials → 401) | `backend/routers/auth.py` (182-224) |
| F4 | **High** | Passwords | No scrypt parameter verification tests | `backend/database.py` (104-115) |
| F5 | **High** | Passwords | No password min-length enforcement tests (6 vs OWASP 8) | `backend/routers/auth.py` (40) |
| F6 | **High** | Passwords | No signup error enumeration test ("Email already registered" reveals email existence) | `backend/routers/auth.py` (138) |
| F7 | **High** | API/Injection | No SQL injection verification tests (parameterized queries — code review only) | `backend/database.py` (138, 151, 159, 183, 212, 223, 261) |
| F8 | **High** | API/Injection | No input length limit tests for `query`, `problem`, `novelty` | `backend/routers/pipeline.py` (20), `backend/routers/scoop.py` (75-77) |
| F9 | **High** | API/Injection | No CORS misconfiguration test (`allow_origins=["*"]` + `allow_credentials=True`) | `backend/main.py` (58-64) |
| F10 | **High** | Exception handler | No exception handler info-leak test (triggers error, verifies response) | `backend/main.py` (90-95) |
| F11 | **High** | Webhook | No webhook signature verification tests (valid/invalid/tampered) | `backend/routers/billing.py` (137-146, 243-256) |
| F12 | **High** | Webhook | No webhook unknown-user / non-CAPTURED status tests | `backend/routers/billing.py` (272-293) |
| F13 | **High** | Rate limiting | No rate limiting tests (signup, scoop-start, checkout all unrate-limited) | `backend/routers/auth.py` (133), `backend/routers/scoop.py` (87), `backend/routers/billing.py` (158) |
| F14 | **Medium** | Secrets | No secrets-leak tests (OpenRouter key, Tap key not returned in responses/logs) | `backend/routers/scoop.py` (28-37), `backend/routers/billing.py` (30-32) |
| F15 | **Medium** | Secrets | No JWT secret fallback behavior test (random per-restart if env var missing) | `backend/routers/auth.py` (31) |
| F16 | **Medium** | Frontend | No frontend XSS tests (`marked.parse` on LLM content, `innerHTML` with user data) | `frontend/app.js` (472-474, 386-391, 446-454) |
| F17 | **Medium** | Frontend | No frontend cookie access test (verify no `document.cookie` read of session token) | `frontend/app.js` (no `document.cookie` access — verify) |
| F18 | **Medium** | Edge cases | No empty-runs edge case test (list runs when no runs exist) | `backend/routers/pipeline.py` (54-70) |
| F19 | **Medium** | Edge cases | No single-run / many-runs rendering tests | `frontend/app.js` (337-361, 383-391) |
| F20 | **Medium** | Edge cases | No phase status combination tests (complete/running/failed/pending) | `backend/routers/pipeline.py` (164-196), `frontend/app.js` (397-441) |
| F21 | **Medium** | Edge cases | No webhook non-CAPTURED status handling test | `backend/routers/billing.py` (272) |
| F22 | **Low** | Brand | No English-only enforcement test (verify no i18n code, RTL, language toggles) | `frontend/index.html`, `frontend/app.js`, `frontend/style.css` |
| F23 | **Low** | Brand | No Nova Labs brand/identity consistency test | `frontend/index.html` (6, 28, 221, 268, 270), `frontend/brand/brand.css` (49) |

---

## Detailed Findings

### F1–F3: Auth Security Tests (CRITICAL — No Tests Exist)

**File:** `backend/routers/auth.py`

**What the code does correctly:**
- `decode_jwt` catches `jwt.PyJWTError` and returns `None` (line 93-94) — tampered tokens are rejected
- `get_current_user` raises 401 for missing token (line 117), invalid token (line 121), and missing user (line 125)
- Session cookie is `httponly=True` (line 148) and `samesite="lax"` (line 149)
- Login error message is generic: "Invalid email or password" (line 161)

**Testing gaps:**
- **F1:** No test for JWT forgery — craft a token with a different secret and verify `decode_jwt` returns `None`. The `create_jwt`/`decode_jwt` helpers are pure functions and trivially testable.
- **F2:** No test for expired JWT rejection — create a token with `exp` in the past and verify `get_current_user` returns 401. JWT expiry is 72 hours (line 84); testing requires constructing a token with an expired timestamp.
- **F3:** No test for auth bypass — call `/api/auth/me`, `/api/auth/runs`, `/api/auth/limits`, `/api/pipeline/start` (if auth were required), `/api/billing/*` without a session cookie or Authorization header and verify 401. Note: `/api/pipeline/runs` and `/api/pipeline/start` are **not protected by auth** (pipeline.py has no `get_current_user` dependency) — this is a design choice but should be documented.

**Cookie `secure=False`** (line 151, 170): Flagged in framework as production config issue. No test needed — this is a deployment concern.

---

### F4–F6: Password / Credential Testing (HIGH — No Tests Exist)

**File:** `backend/database.py` (104-128), `backend/routers/auth.py` (38-46)

**What the code does correctly:**
- `scrypt` with `n=16384, r=8, p=1, dklen=64` (line 107-114) — NIST-recommended minimum `n` met
- Parameterized queries throughout — no SQL injection vector (all `?` placeholders)
- Login error doesn't distinguish missing email from wrong password (line 161)

**Testing gaps:**
- **F4:** No test verifying scrypt parameters are correct — test `_hash_password` produces a 128-char hex string (64 bytes), `_verify_password` returns True for correct password and False for wrong password, and hash is deterministic for same salt.
- **F5:** No test for password min-length enforcement — `SignupRequest.password` has `min_length=6` (line 40). OWASP recommends ≥8. Test that a 5-char password is rejected with 422 and a 6-char password is accepted. This is a **policy gap** (framework gap #10) but the enforcement itself is testable.
- **F6:** No test for signup error enumeration — signup returns "Email already registered" (line 138) which reveals email existence. Test that registering with an existing email returns 409 with this message. The framework recommends generic "Account already exists" but this is a code change, not a test gap per se. The test should verify the current behavior and flag the enumeration risk.

---

### F7–F9: API / Injection Testing (HIGH — No Tests Exist)

**Files:** `backend/database.py`, `backend/routers/pipeline.py`, `backend/routers/scoop.py`, `backend/main.py`

**What the code does correctly:**
- All SQL queries use `?` placeholders — no string formatting in SQL (verified by code review of all `execute` calls in database.py)
- Pydantic models on all request bodies provide type validation

**Testing gaps:**
- **F7:** No SQL injection verification tests. Given the codebase is small and uses parameterized queries throughout, fuzzing is unnecessary — but a test should exist that confirms parameterized query behavior (e.g., attempt to inject via `create_user` with a malicious email string and verify it's stored literally, not executed).
- **F8:** No input length limit tests. `PipelineStartRequest.query` (line 20), `ScoopStartRequest.problem` and `novelty` (lines 75-77) have **no length limits**. A maliciously large payload could cause memory issues or oversized DB entries. Add `max_length` to Pydantic fields and test enforcement.
- **F9:** No CORS misconfiguration test. `main.py` line 60-61 sets `allow_origins=["*"]` with `allow_credentials=True`. This is flagged in the framework as insecure for production. Test that verifies the CORS headers returned by the API. Note: browsers won't actually send credentials with `*` origin, so the practical risk is low, but the configuration is misleading.

---

### F10: Exception Handler Info Leakage (HIGH — No Test Exists)

**File:** `backend/main.py` (90-95)

```python
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "detail": "Internal server error"},
    )
```

**The problem:** `str(exc)` is returned in the response body. If an exception contains internal details (file paths, SQL queries, stack traces), they leak to the client.

**Testing gap:** No test triggers an exception and verifies the response body. A test should:
1. Trigger a known exception (e.g., call an endpoint that raises)
2. Verify the response contains `str(exc)` — confirming the leak
3. Recommend replacing with a generic message + server-side logging

**Example leak scenario:** If `subprocess.run` in `pipeline.py` raises `FileNotFoundError` for `run_pipeline.sh`, `str(exc)` would return something like `[Errno 2] No such file or directory: '/Users/khalid/Desktop-fast/run_pipeline.sh'` — revealing the server's filesystem path.

---

### F11–F12: Webhook Security Tests (HIGH — No Tests Exist)

**File:** `backend/routers/billing.py` (137-146, 243-293)

**What the code does correctly:**
- `_verify_tap_signature` uses `hmac.compare_digest` for constant-time comparison (line 146) ✓
- Signature verification is performed before processing (line 250) ✓
- Returns 401 for invalid signature when `TAP_WEBHOOK_SECRET` is set (line 251) ✓

**Testing gaps:**
- **F11:** No webhook signature tests. Test cases needed:
  - Valid signature → 200, webhook processed
  - Wrong secret → 401
  - Tampered body (modify JSON after signing) → 401
  - Missing signature header → depends on whether `TAP_WEBHOOK_SECRET` is set (if set, `not _verify_tap_signature` returns True → 401; if not set, signature check is skipped)
  - Empty `TAP_WEBHOOK_SECRET` → signature check bypassed (line 139-140 returns False, line 250 skips check when secret is empty)
- **F12:** No webhook edge case tests:
  - Non-CAPTURED status (e.g., "AUTHORIZED", "PENDING") → should be no-op (line 272 only matches CAPTURED)
  - CAPTURED but non-000 response code → should be no-op
  - Webhook with unknown `user_id` (not in DB) → `update_user` returns None but no error raised; verify graceful handling
  - Webhook with missing `udf1`/`udf2` metadata → `user_id` and `plan_id` are empty strings; verify no crash
  - Malformed JSON body → 400 (line 256) — testable

**Replay protection:** No idempotency key or replay protection exists (framework notes this as lower risk for hosted payment flow). Not a test gap per se, but worth documenting.

---

### F13: Rate Limiting Tests (HIGH — No Tests Exist, Rate Limiting Absent)

**Files:** `backend/routers/auth.py` (133), `backend/routers/scoop.py` (87), `backend/routers/billing.py` (158)

**The problem:** No rate limiting on any endpoint. Specific abuse vectors:
- `/api/auth/signup` — account enumeration spam (testable: send N signup requests in rapid succession and verify no rate limiting)
- `/api/scoop-check/start` — LLM cost abuse (each call spawns a background process that calls OpenRouter)
- `/api/billing/create-checkout` — checkout spam

**Testing gap:** No rate limiting tests because no rate limiting exists. The test should document the absence and recommend implementation (e.g., token bucket per IP or per user).

**Additional gap:** `start_pipeline` (pipeline.py line 36-51) does **not** call `check_run_limit` before spawning a subprocess. A user who has exceeded their quota can still start runs. The `/api/auth/limits` endpoint (auth.py line 216-225) reports the limit status but nothing enforces it at pipeline start. This is framework gap #2 (Critical).

---

### F14–F15: Secrets Management Tests (MEDIUM — No Tests Exist)

**Files:** `backend/routers/scoop.py` (28-37), `backend/routers/billing.py` (30-32), `backend/routers/auth.py` (31)

**What the code does correctly:**
- `TAP_API_KEY` is never logged or returned in API responses (verified by code review)
- `OPENROUTER_API_KEY` is never logged or returned in API responses (verified)
- `llm_status` endpoint (health.py line 57-71) only returns `has_openrouter_key: bool` — not the key itself ✓

**Testing gaps:**
- **F14:** No secrets-leak tests. Test cases:
  - Call `llm_status` and verify response does not contain the actual key value
  - Call all endpoints and grep responses for API key patterns
  - Verify error responses from `call_llm` (scoop.py line 72: `return json.dumps({"error": str(e)})`) — if `urllib` raises an exception with the API key in the error message, it could leak. **This is a real risk:** `urllib.error.HTTPError` may include the request URL or headers in its string representation.
- **F15:** No JWT secret fallback test. Line 31: `JWT_SECRET = os.environ.get("OPENRESEARCH_JWT_SECRET", secrets.token_hex(32))`. If the env var is not set, a new random secret is generated on every restart, invalidating all sessions. Test: start the app without the env var, create a session, restart the app, verify the session is invalidated.

**`TAP_POST_URL` from `/home/enigma/.kinox/env`:** Loaded in `billing.py` line 179-182 via `os.environ.get("TAP_POST_URL", ...)`. The kinox env file at `/home/enigma/.kinox/env` does not exist in the current environment (file missing). Test should verify the fallback URL is used when the env var is absent.

---

### F16–F17: Frontend Security Tests (MEDIUM — No Tests Exist)

**File:** `frontend/app.js`

**XSS assessment:**
- `marked.parse(md)` on line 473 — renders LLM-generated markdown into `innerHTML` (line 474). The content is LLM-generated (from idea card), not directly user input, so risk is **low but not zero** — if the LLM output can be influenced to contain malicious markdown/HTML, XSS is possible.
- `renderMathInElement` on line 476 — processes the same LLM content; KaTeX render has had XSS vulnerabilities in the past.
- Run tabs (line 386-391): `r.query?.slice(0,80)` rendered in `title` attribute and `r.query?.slice(0,30)` in button text. These are user-provided queries. The `title` attribute is not executable, but the text content could contain XSS if the browser parses it oddly. **Low risk** — text content via template literal is generally safe.
- History (line 446-454): `r.query || r.run_id` rendered as text content via template literal. Safe.
- Billing plans (line 228-238): Plan data from API rendered via template literal in `innerHTML`. Plan names/features come from the server (trusted). Safe.
- `el.innerHTML = plans.map(p => ...).join('')` on line 238 — server-controlled data, safe.

**What the code does correctly:**
- No `eval()` calls anywhere in app.js ✓
- No `document.write()` calls ✓
- No `document.cookie` access — session token is HTTP-only, JS cannot read it ✓
- No raw user input from form fields inserted into `innerHTML` without sanitization ✓

**Testing gaps:**
- **F16:** No frontend XSS tests. Test cases:
  - Verify `marked.parse` is not called on user input (only on LLM-generated card content)
  - Verify no `innerHTML` assignment with raw user input from form fields
  - Verify `escapeHtml` is used where needed (it's defined in dashboard.py line 202-206 but not in app.js)
- **F17:** No cookie access test. Verify `app.js` never reads `document.cookie` (confirmed by grep — no `document.cookie` in app.js).

---

### F18–F21: Edge Case Tests (MEDIUM — No Tests Exist)

**Files:** `backend/routers/pipeline.py`, `frontend/app.js`, `backend/routers/billing.py`

**Testing gaps:**
- **F18:** No empty-runs test. `list_pipeline_runs` (pipeline.py line 54-70) returns `[]` when `RUN_DIR` doesn't exist (line 57-58) or when no dirs have `query.txt` (line 62). Frontend `render()` (app.js line 347-361) handles empty runs: hides pipeline container, shows empty state. Test: verify API returns `[]` for empty dir, verify frontend shows empty state.
- **F19:** No single-run / many-runs rendering tests. Frontend `renderRunTabs` (line 383-392) uses `runs.map()` — works for 0, 1, or many runs. `renderHistory` (line 443-455) same. Test: render with 0, 1, and 10+ runs and verify correct DOM output.
- **F20:** No phase status combination tests. `_scan_phases` (pipeline.py line 164-196) returns `{"status": "complete"}` or `{"status": "pending"}` for each phase. The frontend stepper (app.js line 397-415) and phases list (line 417-441) handle `complete`, `running`, `failed`, `pending` statuses. Test: render with each status combination and verify correct UI state. **Note:** `running` and `failed` statuses are never set by `_scan_phases` — they're only set by the frontend's own polling. The `_scan_phases` function only returns `complete` or `pending`. This is a mismatch — the frontend expects statuses the backend never produces.
- **F21:** No webhook non-CAPTURED status test. The webhook handler (billing.py line 272) only processes `status == "CAPTURED" and response_code == "000"`. All other statuses fall through to `return {"status": "ok"}` (line 293). Test: send webhooks with AUTHORIZED, PENDING, REFUNDED statuses and verify no side effects (no subscription created, no user updated).

---

### F22–F23: Brand / English-Only Tests (LOW — No Tests Exist)

**Files:** `frontend/index.html`, `frontend/app.js`, `frontend/style.css`, `frontend/brand/brand.css`

**What the code does correctly (verified by code review):**
- `index.html` has `lang="en"` (line 2) ✓
- No `<link>` to i18n resource files ✓
- No language toggle UI element ✓
- No RTL styles (`dir="rtl"`, `unicode-bidi`, `text-align: right` for body) ✓
- No `Intl` API usage for localization ✓
- Brand consistently "Nova Labs" / "NovaLabs" throughout ✓

**Testing gaps:**
- **F22:** No English-only enforcement test. The framework mandates English-only UI and output. Test: scan `index.html`, `app.js`, `style.css` for any i18n code, RTL styles, language selectors, or non-English UI strings. Currently none exist — verify this remains true.
- **F23:** No brand consistency test. Verify "Nova Labs" branding is consistent across `index.html` (title, logo text, footer), `brand.css` (`--brand-name: Nova Labs`), and any other UI surfaces. The dashboard.html (ui.py line 25-256) uses "IdeaFlow" branding — this is a legacy page and not part of the main SPA.

---

## Edge Case: Dashboard HTML XSS Risk

**File:** `backend/routers/dashboard.py` (line 25-256)

The legacy dashboard HTML (served at `/api/pipeline/dashboard-v2`) contains an `escapeHtml` function (line 202-206) and uses it for `run.query` in line 183. However, the `viewCard` function (line 233-240) renders card content via:

```javascript
document.getElementById('card-content').innerHTML = '<pre>' + escapeHtml(data.content || 'Not found') + '</pre>';
```

This uses `escapeHtml` ✓. But the `deleteRun` function (line 246-250) passes `runId` directly into a `confirm()` call — `confirm('Delete run ' + runId + '?')`. Since `runId` comes from the API response (server-controlled), this is safe. **Low risk** — the dashboard is a legacy page, not the main SPA.

---

## Edge Case: `start_pipeline` Returns Fake Run ID

**File:** `backend/routers/pipeline.py` (line 47-51)

```python
return PipelineStartResponse(
    run_id="pending",
    status="started",
    phases={"phase0": {"status": "running"}},
)
```

The `run_id` is hardcoded as `"pending"` — not a real UUID. The frontend `runPipeline` (app.js line 502-506) calls `loadRuns()` after starting and picks `runs[0]?.run_id`. This means the client never actually knows the run ID from the start response — it discovers it by listing runs. **Edge case test:** start a pipeline, verify the response `run_id` is `"pending"`, verify the real run appears in `list_runs` shortly after.

---

## Test Infrastructure Gaps (from Framework Section 7h)

| Gap | Detail |
|---|---|
| No `conftest.py` | No pytest fixtures for test DB, TestClient, or mocks |
| No `pytest` config | No `pytest.ini`, `pyproject.toml` test config, or test runner script |
| No `TESTING.md` | No documentation on how to run tests |
| No CI | No GitHub Actions workflow for running tests |
| No coverage reporting | No `pytest-cov` or coverage config |
| No mock fixtures | No fixtures for OpenRouter or Tap API mocking |

---

## Recommendations (Priority Order)

1. **CRITICAL:** Write auth security tests first — JWT forgery, expired token, auth bypass. These are pure-function tests on `create_jwt`/`decode_jwt`/`get_current_user` and require no external dependencies.

2. **CRITICAL:** Add `check_run_limit` call to `start_pipeline` and `start_scoop_check` before spawning subprocesses. Write a test that verifies a user at their limit cannot start a new run.

3. **HIGH:** Write webhook signature tests — these are the most security-critical tests after auth. Mock the Tap webhook with valid/invalid signatures.

4. **HIGH:** Write exception handler test — trigger an error and verify `str(exc)` is in the response. Then fix the handler to return a generic message.

5. **HIGH:** Add input length limits to `PipelineStartRequest.query`, `ScoopStartRequest.problem`, `ScoopStartRequest.novelty` and write enforcement tests.

6. **MEDIUM:** Write secrets-leak tests — verify no API keys in responses, especially the `call_llm` error path in scoop.py line 72.

7. **MEDIUM:** Write frontend rendering edge case tests (empty runs, single run, many runs, phase status combinations) using jsdom + Vitest.

8. **LOW:** Add brand/English-only enforcement as a regression test in the CI pipeline.

---

## Appendix: What the Code Gets Right (Verified)

These are confirmed by code review and should be documented as passing security checks:

| Check | Status | Location |
|---|---|---|
| Parameterized SQL queries (no string formatting) | PASS | `backend/database.py` all `execute` calls |
| `hmac.compare_digest` for webhook sigs | PASS | `backend/routers/billing.py:146` |
| HTTP-only session cookie | PASS | `backend/routers/auth.py:148` |
| SameSite=Lax cookie | PASS | `backend/routers/auth.py:149` |
| Generic login error ("Invalid email or password") | PASS | `backend/routers/auth.py:161` |
| No `eval()` in frontend | PASS | `frontend/app.js` — grep confirms |
| No `document.write()` in frontend | PASS | `frontend/app.js` — grep confirms |
| No `document.cookie` access in frontend | PASS | `frontend/app.js` — grep confirms |
| `llm_status` returns bool, not key value | PASS | `backend/routers/health.py:67` |
| `scrypt` with n=16384 (NIST minimum) | PASS | `backend/database.py:110` |
| `PRAGMA foreign_keys=ON` | PASS | `backend/database.py:35` |
| Thread-local DB connections | PASS | `backend/database.py:26-36` |
| Allowed-field whitelist in `update_user` | PASS | `backend/database.py:175` |
| Webhook non-CAPTURED status → no-op (not crash) | PASS | `backend/routers/billing.py:272` |
| English-only (no i18n, RTL, language toggles) | PASS | `frontend/index.html`, `app.js`, `style.css` — grep confirms |
| Nova Labs brand consistency | PASS | `frontend/index.html`, `frontend/brand/brand.css` |

---

*End of report. 23 findings across 9 audit areas. All findings are testing gaps — no test infrastructure exists to verify any security property of this codebase.*
