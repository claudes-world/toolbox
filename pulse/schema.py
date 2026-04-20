from __future__ import annotations

from dataclasses import dataclass, field


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


