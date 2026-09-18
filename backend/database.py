"""IdeaFlow — database module.

SQLite-backed user management, run tracking, and usage quotas.
Single-file, zero-config — just works.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────────
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "openresearch.db"

# ── Thread safety ────────────────────────────────────────────────────────────
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local database connection.

    Uses WAL + a generous busy_timeout so concurrent writer threads block
    briefly instead of raising 'database is locked' 500s.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.execute("PRAGMA busy_timeout=30000")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def _commit_with_retry(conn: sqlite3.Connection, retries: int = 8, delay: float = 0.05) -> None:
    """Commit with exponential-backoff retry on 'database is locked'.

    busy_timeout already makes SQLite wait internally; this is a second
    layer for the narrow commit window under thread contention.
    """
    for attempt in range(retries):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))
    conn.commit()


# ── Schema ──────────────────────────────────────────────────────────────────


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            stripe_customer_id TEXT,
            subscription_tier TEXT NOT NULL DEFAULT 'free',
            subscription_status TEXT NOT NULL DEFAULT 'active',
            runs_used INTEGER NOT NULL DEFAULT 0,
            runs_limit INTEGER NOT NULL DEFAULT 10,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            run_type TEXT NOT NULL CHECK(run_type IN ('scoop', 'idea')),
            query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            tokens_used INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            token_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'default',
            last_used_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            stripe_subscription_id TEXT,
            stripe_price_id TEXT,
            tier TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'incomplete',
            current_period_start TEXT,
            current_period_end TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);
        CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_phase TEXT NOT NULL DEFAULT 'phase0',
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            total_duration_sec REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pipeline_phases (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
            phase_key TEXT NOT NULL,
            phase_num INTEGER NOT NULL,
            phase_label TEXT NOT NULL,
            phase_type TEXT NOT NULL DEFAULT 'auto',
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT,
            completed_at TEXT,
            elapsed_seconds REAL NOT NULL DEFAULT 0.0,
            artifacts TEXT NOT NULL DEFAULT '[]',
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(run_id, phase_key)
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_phases_run ON pipeline_phases(run_id);
    """)
    _commit_with_retry(conn)


# ── User helpers ────────────────────────────────────────────────────────────


def _hash_password(password: str) -> tuple[str, str]:
    """Hash a password with a random salt using scrypt."""
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return key.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify a password against a stored hash."""
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return key.hex() == stored_hash


def create_user(email: str, password: str, name: str = "") -> dict[str, Any] | None:
    """Create a new user. Returns user dict or None if email exists."""
    conn = _get_conn()
    try:
        user_id = secrets.token_hex(12)
        pw_hash, salt = _hash_password(password)
        conn.execute(
            "INSERT INTO users (id, email, name, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), name.strip(), pw_hash, salt),
        )
        _commit_with_retry(conn)
        return get_user_by_id(user_id)
    except sqlite3.IntegrityError:
        return None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Get a user by email."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Get a user by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    """Authenticate a user. Returns user dict or None."""
    user = get_user_by_email(email)
    if not user:
        return None
    if _verify_password(password, user["password_hash"], user["salt"]):
        return user
    return None


def update_user(user_id: str, **kwargs) -> dict[str, Any] | None:
    """Update user fields. Returns updated user or None."""
    allowed = {"name", "subscription_tier", "subscription_status", "runs_used", "runs_limit", "stripe_customer_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_user_by_id(user_id)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [user_id]
    conn = _get_conn()
    conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
    _commit_with_retry(conn)
    return get_user_by_id(user_id)


def increment_run_count(user_id: str) -> dict[str, Any] | None:
    """Increment the user's run counter. Returns updated user."""
    conn = _get_conn()
    conn.execute("UPDATE users SET runs_used = runs_used + 1, updated_at = datetime('now') WHERE id = ?", (user_id,))
    _commit_with_retry(conn)
    return get_user_by_id(user_id)


def check_run_limit(user_id: str) -> tuple[bool, int, int]:
    """Check if user can run. Returns (can_run, used, limit)."""
    user = get_user_by_id(user_id)
    if not user:
        return False, 0, 0
    return user["runs_used"] < user["runs_limit"], user["runs_used"], user["runs_limit"]


# ── Run tracking ────────────────────────────────────────────────────────────


def create_run(user_id: str, run_type: str, query: str) -> dict[str, Any]:
    """Create a new run record."""
    conn = _get_conn()
    run_id = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO runs (id, user_id, run_type, query) VALUES (?, ?, ?, ?)",
        (run_id, user_id, run_type, query),
    )
    _commit_with_retry(conn)
    return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def get_user_runs(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get runs for a user, most recent first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def complete_run(run_id: str, tokens_used: int = 0, cost: float = 0.0):
    """Mark a run as completed."""
    conn = _get_conn()
    conn.execute(
        "UPDATE runs SET status = 'complete', tokens_used = ?, cost = ?, completed_at = datetime('now') WHERE id = ?",
        (tokens_used, cost, run_id),
    )
    _commit_with_retry(conn)


# ── API tokens (for programmatic access) ──


def create_api_token(user_id: str, name: str = "default") -> tuple[str, dict[str, Any]]:
    """Create a new API token. Returns (raw_token, token_record)."""
    conn = _get_conn()
    token_id = secrets.token_hex(8)
    raw_token = f"or_{secrets.token_hex(24)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    conn.execute(
        "INSERT INTO api_tokens (id, user_id, token_hash, name) VALUES (?, ?, ?, ?)",
        (token_id, user_id, token_hash, name),
    )
    _commit_with_retry(conn)
    return raw_token, dict(conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone())


def validate_api_token(raw_token: str) -> dict[str, Any] | None:
    """Validate an API token. Returns user dict or None."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if not row:
        return None
    # Update last used
    conn.execute("UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?", (row["id"],))
    _commit_with_retry(conn)
    return get_user_by_id(row["user_id"])


# ── Subscription helpers ──


def create_subscription(
    user_id: str,
    stripe_subscription_id: str = "",
    stripe_price_id: str = "",
    tier: str = "free",
    status: str = "incomplete",
) -> dict[str, Any]:
    """Create a subscription record."""
    conn = _get_conn()
    sub_id = secrets.token_hex(8)
    conn.execute(
        """INSERT INTO subscriptions (id, user_id, stripe_subscription_id, stripe_price_id, tier, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (sub_id, user_id, stripe_subscription_id, stripe_price_id, tier, status),
    )
    _commit_with_retry(conn)
    return dict(conn.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone())


def get_user_subscription(user_id: str) -> dict[str, Any] | None:
    """Get the active subscription for a user."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


# ── Pipeline Run & Phase Tracking ──────────────────────────────────────────

PIPELINE_PHASE_DEFS: list[dict[str, Any]] = [
    {"key": "phase0", "num": 1, "label": "Literature Search", "type": "auto", "dir": "phase0", "file": "lit_results.json"},
    {"key": "phase0_fulltext", "num": 2, "label": "Full-Text Fetch", "type": "auto", "dir": "phase0", "file": "fulltext_cache.json"},
    {"key": "phase1", "num": 3, "label": "Bottleneck ID", "type": "llm", "dir": "phase1", "file": "phase1_output.json"},
    {"key": "phase2_select", "num": 4, "label": "Gap × Pattern Select", "type": "llm", "dir": "phase2_select", "file": "phase2_select_output.json"},
    {"key": "phase2_generate", "num": 5, "label": "Candidate Gen", "type": "llm", "dir": "phase2_generate", "file": "phase2_generate_output.json"},
    {"key": "phase2_coherence", "num": 6, "label": "Coherence Trace", "type": "llm", "dir": "phase2_coherence", "file": "refined_candidate.json"},
    {"key": "phase3_collision", "num": 7, "label": "Collision Check", "type": "auto", "dir": "phase3_collision", "file": "collision_hits.json"},
    {"key": "phase3_critique", "num": 8, "label": "5-Check Audit", "type": "llm", "dir": "phase3_critique", "file": "phase3_critique_output.json"},
    {"key": "phase3_revise", "num": 9, "label": "Revision", "type": "llm", "dir": "phase3_revise", "file": "final_candidate.json"},
    {"key": "phase4_skeleton", "num": 10, "label": "Skeleton", "type": "auto", "dir": "phase4", "file": "phase4_skeleton.json"},
    {"key": "phase4_fill", "num": 11, "label": "Prose Fill", "type": "llm", "dir": "phase4", "file": "fill_map.json"},
    {"key": "phase4_card", "num": 12, "label": "Idea Card", "type": "auto", "dir": "phase4", "file": "idea.std.en.md"},
]


def init_pipeline_run(run_id: str, query: str, user_id: str = "anonymous") -> dict[str, Any]:
    """Initialize or update a pipeline run and its 12 phases in SQLite."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO pipeline_runs (run_id, user_id, query, status, current_phase, started_at, created_at, updated_at)
           VALUES (?, ?, ?, 'running', 'phase0', ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET
               query = excluded.query,
               updated_at = excluded.updated_at""",
        (run_id, user_id, query.strip(), now, now, now),
    )

    for p in PIPELINE_PHASE_DEFS:
        phase_id = f"{run_id}:{p['key']}"
        conn.execute(
            """INSERT OR IGNORE INTO pipeline_phases
               (id, run_id, phase_key, phase_num, phase_label, phase_type, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (phase_id, run_id, p["key"], p["num"], p["label"], p["type"], now, now),
        )
    _commit_with_retry(conn)
    return get_pipeline_run_details(run_id) or {}


def update_pipeline_phase(
    run_id: str,
    phase_key: str,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    elapsed_seconds: float | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Update progress for a single phase and sync the parent run."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    phase_id = f"{run_id}:{phase_key}"

    # Build update fields dynamically
    fields = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now]

    if started_at is not None:
        fields.append("started_at = ?")
        params.append(started_at)
    elif status == "running":
        fields.append("started_at = COALESCE(started_at, ?)")
        params.append(now)

    if completed_at is not None:
        fields.append("completed_at = ?")
        params.append(completed_at)
    elif status in ("complete", "completed", "failed"):
        fields.append("completed_at = COALESCE(completed_at, ?)")
        params.append(now)

    if elapsed_seconds is not None:
        fields.append("elapsed_seconds = ?")
        params.append(elapsed_seconds)

    if artifacts is not None:
        fields.append("artifacts = ?")
        params.append(json.dumps(artifacts))

    if error_message is not None:
        fields.append("error_message = ?")
        params.append(error_message)

    params.append(phase_id)
    conn.execute(f"UPDATE pipeline_phases SET {', '.join(fields)} WHERE id = ?", params)

    # Sync parent run
    run_status = "running"
    if status == "failed":
        run_status = "failed"
    elif phase_key == "phase4_card" and status in ("complete", "completed"):
        run_status = "completed"

    total_duration_row = conn.execute(
        "SELECT SUM(elapsed_seconds) as total FROM pipeline_phases WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    total_sec = total_duration_row["total"] if total_duration_row and total_duration_row["total"] else 0.0

    conn.execute(
        """UPDATE pipeline_runs
           SET current_phase = ?,
               status = CASE WHEN status = 'cancelled' THEN 'cancelled'
                             WHEN ? = 'failed' THEN 'failed'
                             WHEN ? = 'completed' THEN 'completed'
                             ELSE status END,
               completed_at = CASE WHEN ? IN ('completed', 'failed') THEN ? ELSE completed_at END,
               total_duration_sec = ?,
               updated_at = ?
           WHERE run_id = ?""",
        (phase_key, run_status, run_status, run_status, now, total_sec, now, run_id),
    )
    _commit_with_retry(conn)
    return get_pipeline_run_details(run_id)


def cancel_pipeline_run(run_id: str) -> dict[str, Any] | None:
    """Mark a pipeline run as cancelled (terminal). Idempotent no-op on terminal runs.

    Returns the run details dict, or None if the run does not exist.
    Cancelling an already terminal (completed/failed/cancelled) run is a
    no-op success returning current details.
    """
    conn = _get_conn()
    row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    now = datetime.now(timezone.utc).isoformat()
    if (row["status"] or "") in ("completed", "failed", "cancelled"):
        return get_pipeline_run_details(run_id)
    conn.execute(
        """UPDATE pipeline_runs
           SET status = 'cancelled', completed_at = COALESCE(completed_at, ?), updated_at = ?
           WHERE run_id = ?""",
        (now, now, run_id),
    )
    _commit_with_retry(conn)
    return get_pipeline_run_details(run_id)


def delete_pipeline_run(run_id: str) -> bool:
    """Delete a pipeline run and all its phases (FK cascade).

    Returns True when a row existed and was removed, False otherwise.
    Disk cleanup is the caller's job (router owns RUN_DIR).
    """
    conn = _get_conn()
    cur = conn.execute("DELETE FROM pipeline_runs WHERE run_id = ?", (run_id,))
    _commit_with_retry(conn)
    return cur.rowcount > 0


def get_pipeline_run_details(run_id: str) -> dict[str, Any] | None:
    """Get full pipeline run state and all 12 phase records."""
    conn = _get_conn()
    run_row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        return None
    run_dict = dict(run_row)

    phase_rows = conn.execute(
        "SELECT * FROM pipeline_phases WHERE run_id = ? ORDER BY phase_num ASC",
        (run_id,),
    ).fetchall()

    phases_map = {}
    for r in phase_rows:
        p = dict(r)
        try:
            p["artifacts"] = json.loads(p.get("artifacts") or "[]")
        except Exception:
            p["artifacts"] = []
        phases_map[p["phase_key"]] = p

    run_dict["phases"] = phases_map
    run_dict["has_idea_card"] = phases_map.get("phase4_card", {}).get("status") in ("complete", "completed")
    return run_dict


def list_all_pipeline_runs(limit: int = 20, offset: int = 0, include_phases: bool = False) -> list[dict[str, Any]]:
    """List pipeline runs, newest first, with pagination.

    List path is a single JOIN query (no per-run detail calls): one row per
    run with a has_idea_card flag derived from the phase4_card row. Pass
    include_phases=True (detail path) to attach the full 12-phase map —
    that issues 1 extra query per run, so the polled list path keeps it off.
    """
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    conn = _get_conn()
    rows = conn.execute(
        """SELECT r.run_id, r.query, r.status, r.current_phase,
                  r.total_duration_sec, r.created_at,
                  MAX(CASE WHEN p.phase_key = 'phase4_card'
                            AND p.status IN ('complete', 'completed')
                           THEN 1 ELSE 0 END) AS has_card
           FROM pipeline_runs r
           LEFT JOIN pipeline_phases p ON p.run_id = r.run_id
           GROUP BY r.run_id
           ORDER BY r.created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        has_card = bool(d.pop("has_card", 0))
        d["has_idea_card"] = has_card
        if include_phases:
            full = get_pipeline_run_details(d["run_id"])
            if full:
                results.append(full)
        else:
            d["phases"] = {}
            results.append(d)
    return results


def count_pipeline_runs() -> int:
    """Total pipeline run count (for list pagination)."""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) AS n FROM pipeline_runs").fetchone()
    return int(row["n"]) if row else 0


def sync_run_to_disk_and_db(run_id: str, run_dir: Path) -> dict[str, Any]:
    """Scan disk artifacts for a run, compute timestamps/durations, and persist to SQLite + state.json.

    Live-status preservation: phases currently marked 'running' or 'failed'
    in the DB keep their live status/started_at/elapsed/error_message —
    only their artifact lists are refreshed. Disk inference only fills in
    neutral fields (pending→complete transitions and artifact/timing data
    for non-live phases). This keeps every GET from clobbering an active
    worker's progress or wiping a failure's error message.
    """
    query = ""
    query_file = run_dir / "query.txt"
    if query_file.exists():
        query = query_file.read_text(encoding="utf-8").strip()
    if not query:
        query = run_id

    # Ensure DB record exists
    init_pipeline_run(run_id, query)

    # Snapshot live DB state so we don't clobber in-flight work.
    live = get_pipeline_run_details(run_id) or {}
    live_phases: dict[str, Any] = live.get("phases", {}) if isinstance(live, dict) else {}
    live_run_status = live.get("status") if isinstance(live, dict) else None

    # Inspect disk for each phase
    phases_state = {}
    total_elapsed = 0.0
    latest_phase = "phase0"
    all_done = True
    any_failed = False

    for p in PIPELINE_PHASE_DEFS:
        key = p["key"]
        dname = p["dir"]
        fname = p["file"]
        target_file = run_dir / dname / fname

        # Check secondary candidates or alternate outputs if needed
        is_complete = target_file.exists()
        if not is_complete and key == "phase2_coherence":
            is_complete = (run_dir / "phase2_coherence" / "phase2_coherence_output.json").exists()
        elif not is_complete and key == "phase3_revise":
            is_complete = (run_dir / "phase3_revise" / "phase3_revise_output.json").exists()

        artifacts: list[dict[str, Any]] = []
        phase_dir_path = run_dir / dname
        if phase_dir_path.exists() and phase_dir_path.is_dir():
            for f in sorted(phase_dir_path.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    artifacts.append({
                        "name": f.name,
                        "rel_path": f"{dname}/{f.name}",
                        "size": f.stat().st_size,
                        "mtime": f.stat().st_mtime,
                        "ext": f.suffix.lstrip("."),
                    })

        # Calculate timing estimation from file mtime
        disk_status = "complete" if is_complete else "pending"
        elapsed_sec = 0.0
        started_at = None
        completed_at = None

        if is_complete and target_file.exists():
            mtime = target_file.stat().st_mtime
            completed_at = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
            # Estimate start from earliest artifact or parent creation
            if artifacts:
                first_mtime = min(a["mtime"] for a in artifacts)
                started_at = datetime.fromtimestamp(first_mtime, timezone.utc).isoformat()
                elapsed_sec = max(1.0, round(mtime - first_mtime, 1))
            else:
                started_at = completed_at
                elapsed_sec = 2.0
            total_elapsed += elapsed_sec
            latest_phase = key

        # Preserve live worker state: never demote running/failed/awaiting_gate
        # or clear the live error/started fields via a read-path disk sync.
        live_rec = live_phases.get(key) or {}
        live_status = live_rec.get("status")
        if live_status in ("running", "failed", "awaiting_gate"):
            status = live_status
            started_at = live_rec.get("started_at") or started_at
            if live_status == "running":
                completed_at = completed_at
            else:
                completed_at = live_rec.get("completed_at") or completed_at
            elapsed_sec = live_rec.get("elapsed_seconds") or elapsed_sec
            if live_status == "running":
                # A running worker hasn't finished; count its live elapsed.
                total_elapsed += float(elapsed_sec or 0.0)
                latest_phase = key
            elif live_status in ("failed", "awaiting_gate"):
                any_failed = True
                all_done = False
        else:
            status = disk_status
            if not is_complete:
                all_done = False

        if status == "failed":
            any_failed = True
            all_done = False

        if live_status in ("running", "failed", "awaiting_gate"):
            # Refresh artifacts/timing only; keep status + error_message.
            conn = _get_conn()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE pipeline_phases SET artifacts = ?, elapsed_seconds = ?, updated_at = ? WHERE id = ?",
                (json.dumps(artifacts), elapsed_sec, now, f"{run_id}:{key}"),
            )
            _commit_with_retry(conn)
        else:
            update_pipeline_phase(
                run_id=run_id,
                phase_key=key,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_seconds=elapsed_sec,
                artifacts=artifacts,
            )

        phases_state[key] = {
            "key": key,
            "label": p["label"],
            "type": p["type"],
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": elapsed_sec,
            "artifacts": artifacts,
        }

    if live_run_status == "cancelled":
        run_status = "cancelled"
    elif any_failed:
        run_status = "failed"
    elif all_done:
        run_status = "completed"
    elif any(p["status"] in ("complete", "running") for p in phases_state.values()):
        run_status = "running"
    else:
        run_status = "pending"

    state_json = {
        "run_id": run_id,
        "query": query,
        "status": run_status,
        "current_phase": latest_phase,
        "total_duration_sec": total_elapsed,
        "phases": phases_state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        (run_dir / "state.json").write_text(json.dumps(state_json, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not write state.json: {e}")

    return get_pipeline_run_details(run_id) or state_json


# ── Init ──
# NOTE: init_db() is called from the FastAPI lifespan in backend/main.py.
# Do NOT call it on import — import-time side effects break tests and
# create the production DB file as a side effect of any import.