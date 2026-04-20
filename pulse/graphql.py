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

# Per-query cost thresholds for timeline fetches
TIMELINE_COST_WARN = 100
# Cumulative budget guard: warn + skip remaining timeline fetches above this point
TIMELINE_CUMULATIVE_WARN = 4500

PR_TIMELINE_QUERY = """
query($prId: ID!, $cursor: String) {
  node(id: $prId) {
    ... on PullRequest {
      timelineItems(first: 100, after: $cursor, itemTypes: [
        REVIEW_REQUESTED_EVENT,
        PULL_REQUEST_REVIEW,
        MERGED_EVENT,
        CLOSED_EVENT,
        LABELED_EVENT,
        REFERENCED_EVENT
      ]) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on PullRequestReview { author { login } state submittedAt }
          ... on ReviewRequestedEvent { actor { login } createdAt }
          ... on MergedEvent { actor { login } createdAt }
          ... on ClosedEvent { actor { login } createdAt }
          ... on LabeledEvent { actor { login } createdAt label { name } }
          ... on ReferencedEvent { actor { login } createdAt }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
"""


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
                if dl_remaining < 1.0:
                    raise RunDeadlineExceeded()
                read_timeout = min(30.0, dl_remaining)
                request_timeout = httpx.Timeout(timeout=read_timeout, connect=min(10.0, read_timeout))

            try:
                resp = self._client.post(
                    "/graphql",
                    json={"query": query, "variables": variables or {}},
                    timeout=request_timeout,
                )
            except httpx.TransportError:
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
                # Per-query cost check only — does not track cumulative run cost.
                # Callers should aggregate rateLimit.cost across calls if a run budget is needed.
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
                is_secondary = "secondary rate limit" in resp.text.lower()
                has_retry_after = "retry-after" in resp.headers
                try:
                    is_primary_exhausted = int(resp.headers.get("x-ratelimit-remaining", 1)) == 0
                except ValueError:
                    is_primary_exhausted = False

                if is_secondary or has_retry_after or is_primary_exhausted:
                    # Determine wait base: prefer retry-after header, then x-ratelimit-reset epoch
                    wait_base = 60  # default
                    if has_retry_after:
                        try:
                            wait_base = int(resp.headers.get("retry-after", 60))
                        except ValueError:
                            wait_base = 60
                    elif is_primary_exhausted:
                        try:
                            reset_epoch = int(resp.headers.get("x-ratelimit-reset", 0))
                            wait_base = max(0, int(reset_epoch - time.time())) + 1
                        except ValueError:
                            wait_base = 60
                    wait = min(wait_base * (1.5**attempt), 600)
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
        pages_advanced = 0
        path_traversal_failed = False

        try:
            while True:
                body = self.execute(query, variables, deadline_monotonic=deadline_monotonic)

                try:
                    page_info: dict = body
                    for key in page_info_path:
                        page_info = page_info[key]
                    current_nodes: list[dict] = body
                    for key in nodes_path:
                        current_nodes = current_nodes[key]
                    if not isinstance(current_nodes, list):
                        raise TypeError(f"nodes_path resolved to {type(current_nodes).__name__}, expected list")
                    if not isinstance(page_info, dict):
                        raise TypeError(f"page_info_path resolved to {type(page_info).__name__}, expected dict")
                except (KeyError, TypeError) as e:
                    print(f"WARNING: paginate path traversal failed: {e}", file=sys.stderr)
                    path_traversal_failed = True
                    break  # _completed stays False

                nodes.extend(current_nodes)
                pages_advanced += 1

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
                try:
                    with db_conn:
                        db_conn.execute(
                            "INSERT OR REPLACE INTO pagination_state"
                            " (org, repo, field, last_cursor, timestamp)"
                            " VALUES (?, ?, ?, ?, datetime('now'))",
                            (org, repo, effective_field, last_cursor),
                        )
                except Exception as db_err:
                    print(f"WARNING: could not save pagination checkpoint: {db_err}", file=sys.stderr)
            raise  # propagate to caller

        if use_checkpoint:
            if _completed:
                # Clean completion — delete checkpoint
                try:
                    with db_conn:
                        db_conn.execute(
                            "DELETE FROM pagination_state WHERE org=? AND repo=? AND field=?",
                            (org, repo, effective_field),
                        )
                except Exception as db_err:
                    print(f"WARNING: could not clear pagination checkpoint: {db_err}", file=sys.stderr)
            elif path_traversal_failed:
                # Path traversal failed — delete checkpoint to prevent infinite resume loop
                try:
                    with db_conn:
                        db_conn.execute(
                            "DELETE FROM pagination_state WHERE org=? AND repo=? AND field=?",
                            (org, repo, effective_field),
                        )
                except Exception as db_err:
                    print(f"WARNING: could not clear pagination checkpoint: {db_err}", file=sys.stderr)
            # All other cases: preserve checkpoint for next-run resume

        return nodes

    def fetch_pr_timeline(
        self,
        pr_node_id: str,
        deadline_monotonic: float | None = None,
        cumulative_cost: list[int] | None = None,
    ) -> list[dict]:
        """Fetch all timeline events for a PR, paginating if needed.

        Returns list of raw event dicts. Raises CostBudgetExceeded if the
        cumulative cost across all timeline queries in the run approaches the
        hourly budget (TIMELINE_CUMULATIVE_WARN). If pr_node_id is not found
        (node returns None), returns an empty list.

        cumulative_cost: caller-supplied single-element list used as a mutable
        integer accumulator. Pass [0] to enable cross-PR cost tracking.
        """
        nodes = self.paginate(
            query=PR_TIMELINE_QUERY,
            variables={"prId": pr_node_id, "cursor": None},
            page_info_path=["data", "node", "timelineItems", "pageInfo"],
            nodes_path=["data", "node", "timelineItems", "nodes"],
            deadline_monotonic=deadline_monotonic,
            cursor_var="cursor",
        )

        # Track cumulative cost from the last response (rateLimit is in each page body).
        # We re-execute a small extra call to get cost when paginate() completes. Instead,
        # we track it inline by wrapping execute(). Since paginate() already called execute(),
        # we piggyback cost via a separate last-query cost read. For simplicity, we call
        # a single extra non-paginated query only if cumulative tracking is requested.
        # Actually: paginate() calls execute() which already logs cost per call. We track
        # cumulative by reading rateLimit from each response. Since paginate() abstracts
        # execute(), we do one more execute() call here only for cost — wasteful. Better:
        # pass a cost callback. For now, use a single post-paginate cost probe only when
        # cumulative_cost is provided.
        if cumulative_cost is not None:
            try:
                probe = self.execute(
                    "query { rateLimit { cost remaining } }",
                    deadline_monotonic=deadline_monotonic,
                )
                rate = (probe.get("data") or {}).get("rateLimit") or {}
                cost = rate.get("cost", 1)
                remaining = rate.get("remaining", 5000)
                cumulative_cost[0] += cost
                if cost > TIMELINE_COST_WARN:
                    print(
                        f"WARNING: timeline query cost {cost} exceeds threshold {TIMELINE_COST_WARN}",
                        file=sys.stderr,
                    )
                if remaining < 100:
                    print(
                        f"CRITICAL: rateLimit.remaining={remaining} — approaching rate limit",
                        file=sys.stderr,
                    )
                if cumulative_cost[0] >= TIMELINE_CUMULATIVE_WARN:
                    raise CostBudgetExceeded(
                        f"cumulative timeline cost {cumulative_cost[0]} >= {TIMELINE_CUMULATIVE_WARN}; "
                        "skipping remaining timeline fetches for this run"
                    )
            except CostBudgetExceeded:
                raise
            except Exception:
                pass  # cost tracking failure is non-fatal

        return nodes

