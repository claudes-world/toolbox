# Org-Pulse v2 Dashboard — Agent Brief

**Issue:** #50 / #51
**Date:** 2026-04-20
**Audience:** 4 parallel design subagents (one archetype each)

---

## What Is Org-Pulse?

Org-pulse is a GitHub org health monitor. It runs on a schedule (systemd timer) on a Debian VPS, queries the GitHub GraphQL and REST APIs, and captures a snapshot of the following across all repos in one or more orgs:

- Open pull requests (including draft status, staleness, dependabot/renovate flags)
- Open issues (labels, staleness)
- Releases (recent tags)
- Vulnerability alerts (severity, package, age, linked dependabot PR)
- Reviewer activity (who reviewed what, broken out by human logins and known bot buckets)
- Fork upstream drift (commits behind/ahead for forked repos)

Snapshots are stored in SQLite. After each run, the current snapshot is written to `current.json`. The system is in warm-up mode for the first 7 days (not enough history for trend data).

Currently the system outputs a Markdown digest. **The v2 goal is to replace this with a live, interactive React dashboard** served inside the Telegram mini app.

---

## Target Deployment

**Claude Pocket Console (CPC)** — a React/Vite single-page app served as a Telegram Mini App. The CPC is accessed from within Telegram on mobile (primarily iOS and Android) via Telegram's built-in WebKit-based browser.

Key characteristics of the runtime:
- Telegram Mini Apps are iframes inside the Telegram client, rendered by a WebKit engine
- Viewport is typically 375px wide (iPhone SE baseline) on mobile
- The top safe area is provided via CSS variable `--tg-content-safe-area-inset-top` (not `env(safe-area-inset-top)`)
- Telegram injects `window.Telegram.WebApp` for theming and haptics
- Network access is unrestricted — the app can fetch local endpoints or static files

---

## Non-Negotiable Tech Constraints

All 4 design agents MUST comply with these constraints. Deviations require explicit justification in `design.md`.

1. **React functional components + hooks only.** No class components.
2. **TypeScript preferred** (`.tsx`). JavaScript fallback (`.jsx`) is acceptable. If TypeScript: must pass `tsc --noEmit` without errors.
3. **Telegram WebKit CSS baseline:**
   - `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`
   - Top safe area: `padding-top: var(--tg-content-safe-area-inset-top, 0px)`
   - CSS reset: `modern-normalize` or equivalent
   - Min-width target: 375px viewport (scale up gracefully)
4. **Data-viz library allowed** (Recharts, Visx, Chart.js, or similar). Pick one and stay consistent.
5. **Utility CSS allowed** (Tailwind or PicoCSS). Pick one and stay consistent. No inline styles for layout.
6. **No Redux, Zustand, or global state libraries.** React built-ins only (`useState`, `useReducer`, `useContext`).
7. **Data source:** fixture file at `~/claudes-world/tmp/20260420-dashboard-fixture.json`. Import it as a JSON module OR fetch it from a relative URL. Do not embed the full JSON inline in component code — import it.
8. **No vanilla HTML entry points.** The deliverable is a `.tsx` / `.jsx` component, not an `index.html`.

---

## Data Shape

The dashboard consumes `20260420-dashboard-fixture.json`. The full file is the authoritative source. The schema summary below is for orientation:

```typescript
interface Fixture {
  snapshot_id: string;
  captured_at_et: string;           // "2026-04-20 21:30 ET"
  capture_status: "success" | "partial" | "failed";
  duration_ms: number;
  repos_succeeded: number;
  repos_failed: number;
  repos_partial: number;
  warm_up_active: boolean;          // true if < 7 days of history
  warm_up_fill_date: string | null; // ISO date when window fills, e.g. "2026-04-27"

  reviewer_activity_7d: {
    // Keys: "copilot" | "gemini-ca" | "claude-subagent" | "dependabot" | "renovate" | "human:<login>"
    [bucket: string]: {
      total: number;
      approved: number;
      change_requested: number;
      commented: number;
      dismissed: number;
    };
  };

  repos: RepoSnapshot[];
}

interface RepoSnapshot {
  org: string;
  name: string;
  is_fork: boolean;
  is_archived: boolean;
  capture_status: "success" | "partial" | "failed";

  field_statuses: {
    // Keys: "prs" | "issues" | "releases" | "vulnerability_alerts" | "upstream" | etc.
    [field: string]: {
      status: "success" | "failed" | "partial" | "scope_missing";
      error_note: string | null;
    };
  };

  upstream: {
    status: "success" | "parent_unavailable";
    commits_behind: number;
    commits_ahead: number;
  } | null;  // null if is_fork=false

  vulnerability_alerts: VulnAlert[] | null;  // null if scope_missing

  prs: PR[];
  issues: Issue[];
  releases: Release[];
}

interface VulnAlert {
  severity: "CRITICAL" | "HIGH" | "MODERATE" | "LOW";
  ghsa_id: string;
  package_name: string;
  ecosystem: string;
  age_days: number;
  dependabot_pr_number: number | null;
}

interface PR {
  number: number;
  title: string;
  author: string;
  is_draft: boolean;
  is_dependabot: boolean;
  is_renovate: boolean;
  stalled: boolean;       // true if hours_idle > 72
  hours_idle: number;
  updated_at: string;     // ISO timestamp
}

interface Issue {
  number: number;
  title: string;
  labels: string[];
  stalled: boolean;
  hours_idle: number;
}

interface Release {
  tag_name: string;
  name: string;
  is_prerelease: boolean;
  created_at: string;   // ISO timestamp
}
```

**Edge cases present in the fixture (design for these):**
- `warm_up_active: true` — no trend data available yet; show a banner or inline notice
- `capture_status: "failed"` on a repo — show failure state, not empty data
- `capture_status: "partial"` — some fields succeeded, check `field_statuses` per field
- `vulnerability_alerts: null` — scope missing, not "no vulns"; must NOT show "0 vulns"
- `stalled: true` + `hours_idle > 72` — needs visual distinction from active PRs
- `is_draft: true` — draft PRs need different treatment than open review-ready PRs
- `is_dependabot: true` / `is_renovate: true` — bot PRs, deprioritize in human review views

---

## Archetype Assignments

**Each agent produces one archetype. Archetypes must be meaningfully different — not minor style variations.**

The 4 divergence axes are: information density, visual modality, interaction model, information hierarchy.

---

### Agent 1 — minimalist-text

**Output directory:** `~/claudes-world/tmp/20260420-dashboard-agent-1/`

**Philosophy:** Almost-terminal. Maximum information density. No charts, no graphics, no icons (except severity color). Everything as fast-scan text. Inspiration: `htop`, `k9s`, Grafana table view, `lazygit`.

Design constraints:
- Monospace or near-monospace font throughout
- Color ONLY for severity signals (red = CRITICAL/FAILED, yellow = HIGH/PARTIAL, green = healthy)
- No data visualization beyond text and color
- All repos visible in a single scrollable list — no pagination, no collapse-by-default
- Every metric as a number, not a bar or chart

Interaction model:
- Keyboard-navigable rows where Telegram WebKit allows focus management
- Tap/click a repo row to expand inline detail
- No modals — expansion is in-place

---

### Agent 2 — visual-chart

**Output directory:** `~/claudes-world/tmp/20260420-dashboard-agent-2/`

**Philosophy:** Chart-forward. Visual triage in 5 seconds. Sparklines for PR age distribution, bar charts for reviewer activity, severity badge counts. Use a data-viz library heavily.

Design constraints:
- Information density intentionally LOWER than Agent 1 — tradeoff is speed of visual scan
- Lead with summary charts before repo list
- Color as a primary encoding, not just severity
- Reviewer activity must appear as a chart (bar or horizontal bar), not a table

Interaction model:
- Tap chart elements to drill into the underlying repos/PRs
- Repo cards below the charts; tap to expand
- No heavy table UI

---

### Agent 3 — tabular-terminal

**Output directory:** `~/claudes-world/tmp/20260420-dashboard-agent-3/`

**Philosophy:** Dense sortable/filterable table. Excel-like. Each repo is a row. Columns for key metrics: open PR count, stalled PR count, vuln count, hours idle on oldest PR, capture status.

Design constraints:
- Pure table — no charts
- Sortable columns (tap header to sort asc/desc)
- Filter bar at top (text filter on repo name; status filter for capture_status)
- Horizontal scroll acceptable for wide columns on mobile
- Fixed first column (repo name) on horizontal scroll

Interaction model:
- Tap column header to sort
- Tap row to expand repo detail (PRs, issues, vulns) in a slide-up panel or inline expansion

---

### Agent 4 — alert-first

**Output directory:** `~/claudes-world/tmp/20260420-dashboard-agent-4/`

**Philosophy:** Inverted information hierarchy. Start with what is broken. Alerts and failures at the very top — scope_missing fields, CRITICAL vulns, stalled PRs (hours_idle > 72), partial/failed captures. Healthy repos are collapsed or hidden by default.

Design constraints:
- No summary overview at top — jump straight into the alert list
- Alerts sorted by severity: capture failures > CRITICAL vulns > stalled PRs > HIGH vulns > partial captures
- Healthy section exists but is below the fold and collapsed by default ("N repos healthy — show")
- Each alert card shows enough context to act (which repo, which PR/vuln, age)

Interaction model:
- Alert list with expand-per-alert for context
- Healthy section toggle at bottom
- No charts

---

## Required Artifacts (3 per agent, all mandatory)

Each agent produces exactly these 3 files in their output directory:

| File | Length | Content |
|------|--------|---------|
| `design.md` | 300-500 words | Information hierarchy, interaction model, mobile-first decisions, what this design uniquely optimizes for |
| `dashboard.tsx` (or `.jsx`) | no limit | Working React component consuming the fixture. Syntactically valid. Imports fixture as JSON module. |
| `rationale.md` | 200-400 words | Why this archetype is best for pulse users — both agents checking dashboards and Liam doing morning review |

**dashboard.tsx must:**
- Import the fixture: `import fixture from "../20260420-dashboard-fixture.json";` (adjust relative path as needed)
- Export a default React component
- Render meaningfully with the fixture data (not placeholder lorem ipsum)
- Pass `tsc --noEmit` if TypeScript

---

## Self-Containment Check

You have everything you need above. Do not ask for clarification — make reasonable design decisions and explain them in `design.md`.
