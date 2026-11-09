"""A2 live verification: fire a REAL run and confirm the DB row.

Not a unit test. Drives the real Gateway app in-process (real AuthMiddleware,
real RunManager/worker, real LLM provider over the network, real SQLite
database at backend/.deer-flow/data/deerflow.db), then queries the row that
was just written.

The point is the plan's requirement: this write path has never been proven by
a single real production run. 869 existing rows all have
token_usage_by_model='{}' and model_name NULL, because the column itself was
only created 2026-08-03 (8a89d8b4c) while the newest run is 2026-07-22.

Usage (from backend/, with .env loaded so ARK_API_KEY resolves):
    uv run python scripts/a2_live_run_check.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(".deer-flow/data/deerflow.db")


def _snapshot() -> tuple[int, int, int]:
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    try:
        c = con.cursor()
        runs = c.execute("select count(*) from runs").fetchone()[0]
        buckets = c.execute("select count(*) from runs where token_usage_by_model not in ('', '{}')").fetchone()[0]
        named = c.execute("select count(*) from runs where model_name is not null").fetchone()[0]
        return runs, buckets, named
    finally:
        con.close()


async def main() -> int:
    before = _snapshot()
    print(f"BEFORE: runs={before[0]} non_empty_buckets={before[1]} model_name_not_null={before[2]}")

    from httpx import ASGITransport, AsyncClient

    from app.gateway.app import app
    from app.gateway.internal_auth import create_internal_auth_headers

    # ASGITransport does not run the lifespan, and the stream bridge / run
    # manager are built there — without this the run 503s before it starts.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=300.0) as client, app.router.lifespan_context(app):
        # Internal auth: same process, so the process-local token matches.
        headers = create_internal_auth_headers()

        # CSRF: the middleware requires the header on state-changing requests
        # and seeds the cookie from this public GET (see the repo note on
        # csrf_cookie_only_on_auth_post — without this every write 403s).
        csrf = await client.get("/api/v1/auth/csrf-token", headers=headers)
        token = csrf.cookies.get("csrf_token") or client.cookies.get("csrf_token")
        if token:
            headers["X-CSRF-Token"] = token
        print(f"csrf token acquired: {bool(token)}")

        body = {
            "input": {"messages": [{"role": "user", "content": "Reply with exactly the word: pong"}]},
            "config": {"configurable": {"subagent_enabled": False, "is_plan_mode": False}},
        }
        print("firing real run against /api/runs/wait ...")
        resp = await client.post("/api/runs/wait", json=body, headers=headers)
        print("HTTP", resp.status_code)
        if resp.status_code != 200:
            print("BODY:", resp.text[:800])

    after = _snapshot()
    print(f"AFTER:  runs={after[0]} non_empty_buckets={after[1]} model_name_not_null={after[2]}")

    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    try:
        row = con.execute("select run_id, model_name, status, total_tokens, llm_call_count, token_usage_by_model, created_at from runs order by created_at desc limit 1").fetchone()
    finally:
        con.close()

    print("\nNEWEST ROW")
    print("  run_id:      ", row[0])
    print("  model_name:  ", row[1])
    print("  status:      ", row[2])
    print("  total_tokens:", row[3])
    print("  llm_calls:   ", row[4])
    print("  by_model:    ", row[5])
    print("  created_at:  ", row[6])

    bucket = json.loads(row[5]) if row[5] else {}
    ok = bool(bucket) and row[1] is not None and after[0] > before[0]
    has_cache = any("cache_read_tokens" in v for v in bucket.values() if isinstance(v, dict))
    print(f"\nVERDICT: new_run={after[0] > before[0]} bucket_non_empty={bool(bucket)} model_name_set={row[1] is not None} cache_read_present={has_cache}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
