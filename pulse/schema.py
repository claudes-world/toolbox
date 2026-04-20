from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass
class AlertData:
    severity: str | None
    ghsa_id: str | None
    package_name: str | None
    ecosystem: str | None
    age_days: int | None
    dependabot_pr_number: int | None
