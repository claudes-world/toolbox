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


class RetriesExhausted(Exception):
    pass


# Backward-compat alias
RateLimitExhausted = RetriesExhausted


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

            # Compute per-request timeout respecting deadline
            request_timeout = httpx.Timeout(timeout=30.0, connect=10.0)
            if deadline_monotonic is not None:
                dl_remaining = deadline_monotonic - time.monotonic()
                if dl_remaining <= 0:
                    raise RunDeadlineExceeded()
                read_timeout = max(1.0, min(30.0, dl_remaining))
                request_timeout = httpx.Timeout(timeout=read_timeout, connect=min(10.0, read_timeout))

            try:
                resp = self._client.post(
                    "/graphql",
                    json={"query": query, "variables": variables or {}},
                    timeout=request_timeout,
                )
            except (httpx.NetworkError, httpx.TimeoutException):
                wait = min(30, 2**attempt)
                if deadline_monotonic is not None:
                    dl_remaining = deadline_monotonic - time.monotonic()
                    if dl_remaining <= 0:
                        raise RunDeadlineExceeded()
                    wait = min(wait, max(0, dl_remaining - 1))
                if attempt < max_retries - 1:
                    time.sleep(wait)
                continue

            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    # treat malformed body as transient
                    wait = min(30, 2**attempt)
                    if deadline_monotonic is not None:
                        dl_remaining = deadline_monotonic - time.monotonic()
                        if dl_remaining <= 0:
                            raise RunDeadlineExceeded()
                        wait = min(wait, max(0, dl_remaining - 1))
                    if attempt < max_retries - 1:
                        time.sleep(wait)
                    continue

                if not isinstance(body, dict):
                    # non-dict JSON body (null, array) — treat as transient
                    wait = min(30, 2**attempt)
                    if deadline_monotonic is not None:
                        dl_remaining = deadline_monotonic - time.monotonic()
                        if dl_remaining <= 0:
                            raise RunDeadlineExceeded()
                        wait = min(wait, max(0, dl_remaining - 1))
                    if attempt < max_retries - 1:
                        time.sleep(wait)
                    continue

                if body.get("data") is None and body.get("errors"):
                    print(
                        f"WARNING: GraphQL returned null data with errors: {body['errors'][:2]}",
                        file=sys.stderr,
                    )

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

                try:
                    remaining = int(resp.headers.get("x-ratelimit-remaining", 5000))
                except ValueError:
                    remaining = 5000
                if remaining < 100:
                    try:
                        reset = int(resp.headers.get("x-ratelimit-reset", 0))
                    except ValueError:
                        reset = 0
                    wait = min(max(0, reset - time.time()) + 1, 120)
                    if deadline_monotonic is not None:
                        dl_remaining = deadline_monotonic - time.monotonic()
                        if dl_remaining <= 0:
                            raise RunDeadlineExceeded()
                        wait = min(wait, max(0, dl_remaining - 1))
                    time.sleep(wait)

                return body

            if resp.status_code in (403, 429):
                if "secondary rate limit" in resp.text.lower():
                    try:
                        retry_after = int(resp.headers.get("retry-after", 60))
                    except ValueError:
                        retry_after = 60
                    wait = min(retry_after * (1.5**attempt), 600)
                    if deadline_monotonic is not None:
                        dl_remaining = deadline_monotonic - time.monotonic()
                        if dl_remaining <= 0:
                            raise RunDeadlineExceeded()
                        wait = min(wait, max(0, dl_remaining - 1))
                    if attempt < max_retries - 1:
                        time.sleep(wait)
                    continue
                else:
                    raise RuntimeError(f"auth or unexpected 4xx: {resp.status_code} {resp.text[:500]}")

            if resp.status_code >= 500:
                wait = min(30, 2**attempt)
                if deadline_monotonic is not None:
                    dl_remaining = deadline_monotonic - time.monotonic()
                    if dl_remaining <= 0:
                        raise RunDeadlineExceeded()
                    wait = min(wait, max(0, dl_remaining - 1))
                if attempt < max_retries - 1:
                    time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise RuntimeError(f"gql {resp.status_code}: {resp.text[:500]}")

        raise RetriesExhausted(f"max retries ({max_retries}) exceeded")

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
        fingerprint: str = "",
    ) -> list[dict]:
        """Walk a paginated GraphQL connection, returning all collected nodes.

        Checkpoint semantics: when interrupted by an exception (deadline, rate limit),
        the cursor of the last successfully fetched page is saved to pagination_state so
        the next run resumes from there instead of restarting from page 1. Checkpoint is
        deleted on clean completion (hasNextPage=False).

        On resume, only nodes from the saved cursor forward are returned. Earlier pages
        from the interrupted run are permanently lost — callers must handle partial results.

        fingerprint: optional string included in the checkpoint key alongside field.
        Change it when the query shape or filters change to avoid resuming with a stale
        cursor into a different result set. If repo is renamed, use a new fingerprint.
        """
        variables = dict(variables or {})
        use_checkpoint = db_conn is not None and org and repo and field
        # Include fingerprint in checkpoint key to prevent stale-cursor resume on query change
        effective_field = f"{field}:{fingerprint}" if fingerprint else field

        if use_checkpoint:
            row = db_conn.execute(
                "SELECT last_cursor FROM pagination_state WHERE org=? AND repo=? AND field=?",
                (org, repo, effective_field),
            ).fetchone()
            if row:
                variables[cursor_var] = row[0]

        nodes: list[dict] = []
        _completed = False
        last_cursor: str | None = None  # last successfully fetched endCursor
        last_body: dict | None = None   # last response body from execute()

        try:
            while True:
                last_body = self.execute(query, variables, deadline_monotonic=deadline_monotonic)

                try:
                    page_info: dict = last_body
                    for key in page_info_path:
                        page_info = page_info[key]
                    current_nodes: list[dict] = last_body
                    for key in nodes_path:
                        current_nodes = current_nodes[key]
                    if not isinstance(current_nodes, list):
                        raise TypeError(f"nodes_path resolved to {type(current_nodes).__name__}, expected list")
                    if not isinstance(page_info, dict):
                        raise TypeError(f"page_info_path resolved to {type(page_info).__name__}, expected dict")
                except (KeyError, TypeError) as e:
                    print(f"WARNING: paginate path traversal failed: {e}", file=sys.stderr)
                    break  # _completed stays False

                nodes.extend(current_nodes)

                end_cursor = page_info.get("endCursor")
                if end_cursor:
                    last_cursor = end_cursor  # track for checkpoint-on-interrupt

                if not page_info.get("hasNextPage"):
                    _completed = True
                    break
                if end_cursor is None:
                    print("WARNING: hasNextPage=True but endCursor=None — pagination truncated", file=sys.stderr)
                    break  # _completed stays False: checkpoint preserved

                variables[cursor_var] = end_cursor

        except (RunDeadlineExceeded, RetriesExhausted, CostBudgetExceeded, RuntimeError):
            # Interrupted — save cursor so next run resumes instead of restarting from page 1
            if use_checkpoint and last_cursor:
                with db_conn:
                    db_conn.execute(
                        "INSERT OR REPLACE INTO pagination_state"
                        " (org, repo, field, last_cursor, timestamp)"
                        " VALUES (?, ?, ?, ?, datetime('now'))",
                        (org, repo, effective_field, last_cursor),
                    )
            raise  # propagate to caller

        if use_checkpoint:
            if _completed:
                # Clean completion — remove checkpoint
                with db_conn:
                    db_conn.execute(
                        "DELETE FROM pagination_state WHERE org=? AND repo=? AND field=?",
                        (org, repo, effective_field),
                    )
            else:
                # Broke out without completing — check if last response had errors (bad cursor)
                # Delete checkpoint to prevent infinite loop on next resume
                last_errors = (last_body or {}).get("errors") if last_body else None
                if last_errors:
                    with db_conn:
                        db_conn.execute(
                            "DELETE FROM pagination_state WHERE org=? AND repo=? AND field=?",
                            (org, repo, effective_field),
                        )

        return nodes
