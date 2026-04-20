from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class UpstreamStatus(BaseModel):
    status: str  # "success" | "parent_unavailable" | "failed" | "not_fork"
    commits_behind: int | None = None
    commits_ahead: int | None = None
    recent_upstream_releases: list[dict] | None = None
    error_note: str | None = None


class VulnerabilityAlert(BaseModel):
    severity: str           # CRITICAL | HIGH | MODERATE | LOW (verbatim from GitHub)
    ghsa_id: str
    package_name: str
    ecosystem: str
    age_days: int
    dependabot_pr_number: int | None = None


@dataclass
class ReviewEvent:
    type: str  # __typename: PULL_REQUEST_REVIEW, REVIEW_REQUESTED_EVENT, MERGED_EVENT, CLOSED_EVENT, LABELED_EVENT, REFERENCED_EVENT
    author: str | None
    state: str | None = None      # for PULL_REQUEST_REVIEW: APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
    label: str | None = None      # for LABELED_EVENT
    submitted_at: str | None = None
    created_at: str | None = None


@dataclass
class FieldStatus:
    status: str  # success | partial | disabled | failed
    error_note: str | None = None


@dataclass
class RepoData:
    org: str
    name: str
    default_branch: str | None
    is_fork: bool
    is_archived: bool
    has_issues_enabled: bool
    parent_owner: str | None
    parent_name: str | None
    parent_is_deleted: bool
    capture_status: str
    field_statuses: dict[str, FieldStatus] = field(default_factory=dict)
    upstream: dict | None = None
    vulnerability_alerts: list | None = None
    parent_default_branch: str | None = None


@dataclass
class PRData:
    number: int
    title: str
    author: str | None
    created_at: str | None
    updated_at: str | None
    is_draft: bool
    is_dependabot: bool
    is_renovate: bool
    hours_idle: float | None
    stalled: bool
    node_id: str | None = None
    review_events: list | None = None


@dataclass
class IssueData:
    number: int
    title: str
    author: str | None
    created_at: str | None
    updated_at: str | None
    labels: list[str]
    hours_idle: float | None
    stalled: bool


@dataclass
class ReleaseData:
    tag_name: str
    name: str | None
    created_at: str | None
    is_prerelease: bool


