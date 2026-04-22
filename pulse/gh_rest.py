from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from pulse.ipv4 import apply_ipv4_patch

logger = logging.getLogger(__name__)

_RATELIMIT_WARN_THRESHOLD = 10


class GHRestClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, force_ipv4: bool = True) -> None:
        if force_ipv4:
            apply_ipv4_patch()
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def _check_ratelimit_header(self, resp: httpx.Response) -> None:
        try:
            remaining = int(resp.headers.get("x-ratelimit-remaining", 100))
        except (ValueError, TypeError):
            return
        if remaining < _RATELIMIT_WARN_THRESHOLD:
            logger.warning(
                "GitHub REST rate limit remaining=%d — approaching limit", remaining
            )

    def compare_fork_upstream(
        self,
        fork_owner: str,
        fork_repo: str,
        fork_default_branch: str,
        parent_owner: str,
        parent_default_branch: str,
    ) -> dict:
        """Compare fork to upstream using GitHub REST compare endpoint.

        Returns upstream status dict for repos.upstream JSON blob.
        NEVER hardcodes branch names — uses captured default_branch values.
        """
        encoded_fork_branch = quote(fork_default_branch, safe="")
        encoded_parent_branch = quote(parent_default_branch, safe="")
        url = (
            f"/repos/{fork_owner}/{fork_repo}/compare"
            f"/{parent_owner}:{encoded_parent_branch}...{fork_owner}:{encoded_fork_branch}"
        )
        resp = self._client.get(url)
        self._check_ratelimit_header(resp)
        if resp.status_code == 404:
            return {"status": "parent_unavailable", "error_note": resp.text[:200]}
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": "success",
            "commits_behind": data.get("behind_by", 0),
            "commits_ahead": data.get("ahead_by", 0),
            "recent_upstream_releases": [],
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GHRestClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
