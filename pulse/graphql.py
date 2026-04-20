from __future__ import annotations

import os
import sqlite3
import sys
import time

import httpx

from pulse.ipv4 import apply_ipv4_patch

COST_WARN_THRESHOLD = 10
COST_ABORT_THRESHOLD = 50
DEFAULT_DEADLINE_SEC = int(os.environ.get("PULSE_RUN_DEADLINE_SEC", "1200"))


class RateLimitExhausted(Exception):
    pass


class RunDeadlineExceeded(Exception):
    pass


class CostBudgetExceeded(Exception):
    pass


def make_deadline(deadline_sec: int = DEFAULT_DEADLINE_SEC) -> float:
    return time.monotonic() + deadline_sec


class GraphQLClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        force_ipv4: bool = True,
    ) -> None:
        if force_ipv4:
            apply_ipv4_patch()
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(timeout=30, connect=10),
            http2=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GraphQLClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def execute(
        self,
        query: str,
        variables: dict | None = None,
        deadline_monotonic: float | None = None,
        max_retries: int = 5,
    ) -> dict:
        for attempt in range(max_retries):
            if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
                raise RunDeadlineExceeded()

            try:
                resp = self._client.post(
                    "/graphql",
                    json={"query": query, "variables": variables or {}},
                )
            except (httpx.NetworkError, httpx.TimeoutException):
                time.sleep(min(30, 2**attempt))
                continue

            if resp.status_code == 200:
                body = resp.json()

                rate_limit = (body.get("data") or {}).get("rateLimit") or {}
                cost = rate_limit.get("cost", 0)
                if cost > COST_ABORT_THRESHOLD:
                    raise CostBudgetExceeded(
                        f"query cost {cost} exceeds abort threshold {COST_ABORT_THRESHOLD}"
                    )
                if cost > COST_WARN_THRESHOLD:
                    print(
                        f"WARNING: query cost {cost} exceeds warn threshold {COST_WARN_THRESHOLD}",
                        file=sys.stderr,
                    )

                remaining = int(resp.headers.get("x-ratelimit-remaining", 5000))
                if remaining < 100:
                    reset = int(resp.headers.get("x-ratelimit-reset", 0))
                    wait = min(max(0, reset - time.time()) + 1, 120)
                    time.sleep(wait)

                return body

            if resp.status_code in (403, 429):
                if "secondary rate limit" in resp.text.lower():
                    retry_after = int(resp.headers.get("retry-after", 60))
                    wait = min(retry_after * (1.5**attempt), 600)
                    time.sleep(wait)
                    continue

            if resp.status_code >= 400:
                raise RuntimeError(f"gql {resp.status_code}: {resp.text[:500]}")

        raise RateLimitExhausted()

    def paginate(
        self,
        query: str,
        variables: dict,
        page_info_path: list[str],
        nodes_path: list[str],
        deadline_monotonic: float | None = None,
        db_conn: sqlite3.Connection | None = None,
        org: str = "",
        repo: str = "",
        field: str = "",
        cursor_var: str = "cursor",
    ) -> list[dict]:
        use_checkpoint = db_conn is not None and org and repo and field

        if use_checkpoint:
            row = db_conn.execute(
                "SELECT last_cursor FROM pagination_state WHERE org=? AND repo=? AND field=?",
                (org, repo, field),
            ).fetchone()
            if row:
                variables[cursor_var] = row[0]

        nodes: list[dict] = []

        while True:
            try:
                body = self.execute(query, variables, deadline_monotonic=deadline_monotonic)

                page_info: dict = body
                for key in page_info_path:
                    page_info = page_info[key]

                current_nodes: list[dict] = body
                for key in nodes_path:
                    current_nodes = current_nodes[key]

                nodes.extend(current_nodes)

            except (KeyError, TypeError):
                return nodes

            end_cursor = page_info.get("endCursor")

            if use_checkpoint and end_cursor:
                with db_conn:
                    db_conn.execute(
                        "INSERT OR REPLACE INTO pagination_state"
                        " (org, repo, field, last_cursor, timestamp)"
                        " VALUES (?, ?, ?, ?, datetime('now'))",
                        (org, repo, field, end_cursor),
                    )

            if not page_info.get("hasNextPage") or end_cursor is None:
                break

            variables[cursor_var] = end_cursor

        if use_checkpoint:
            with db_conn:
                db_conn.execute(
                    "DELETE FROM pagination_state WHERE org=? AND repo=? AND field=?",
                    (org, repo, field),
                )

        return nodes
