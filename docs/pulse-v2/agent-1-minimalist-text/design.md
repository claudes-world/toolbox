# Design Doc — Agent 1: minimalist-text

## Core Philosophy

Terminal-first. Every pixel earns its place by conveying information. No decorative chrome. The dashboard is a read-optimized triage surface — the user scans it the way an sre reads `htop`: left-to-right, high-severity things jump out by color, everything else is white noise in the periphery until needed.

## Information Hierarchy

**Header strip** — snapshot metadata in one line: timestamp, capture status, warm-up banner if active. Nothing below this is global context — it all belongs to a repo.

**Reviewer activity table** — compact: reviewer name, total reviews, approval rate (%), change-requested count. Human reviewers listed first, bots below a separator. This is the only global metric worth seeing before the repo list.

**Repo list** — all repos in a single scrollable column, no pagination, no collapse. Each repo row is one line containing:
- `[STATUS]` — `OK`, `PART`, `FAIL` in color
- repo name (monospace, fixed width)
- PR count / stalled count / issue count
- vuln summary: `Cx Hx Mx` where x is count per severity; `scope?` if null
- upstream drift if fork: `↓3 ↑0`
- latest release tag

Tapping a row expands inline detail: PR list (each PR is one line — number, author, title truncated, idle time, DRAFT / STALLED badges), issue list, vuln list with package name and age, field_statuses for partial repos.

## Interaction Model

Single-tap to expand a repo row in-place. Tap again (or tap another row) to collapse. No modals. No navigation. The viewport is one continuous document. Keyboard focus follows row expansion on platforms that support it.

Expansion is immediate — no animation, no slide. Terminal emulators don't animate; this doesn't either.

## Mobile-First Decisions

**375px baseline.** Repo name column width is capped at 18 chars with ellipsis. All metric columns are right-aligned and take fixed widths so they align vertically across rows — like columns in `ps aux`. The header uses `env()` safe-area padding via the Telegram CSS variable.

Font: `ui-monospace, 'Cascadia Code', 'Fira Code', monospace`. Falls back gracefully on iOS/Android to their system monospace. Font size: 12px body, 11px secondary (PR/issue lines inside expanded rows). Line height 1.4 — tight but readable.

Horizontal overflow is avoided by truncating titles with ellipsis rather than enabling scroll. Only expanded detail rows show more text, and they wrap naturally.

## Color Usage

Strictly severity-only:
- Red (`#f87171`) — CRITICAL vulns, FAILED capture, STALLED PRs
- Yellow (`#fbbf24`) — HIGH vulns, PARTIAL capture, HIGH-priority issues
- Green (`#4ade80`) — healthy status, OK capture
- Dim (`#6b7280`) — bot PRs (dependabot/renovate), secondary metadata
- Default foreground (`#e5e7eb`) — everything else

Background is near-black (`#0f1117`). No gradients, no shadows, no borders thicker than 1px.

## What This Design Uniquely Optimizes For

**Time to first alert.** A developer or agent checking morning pulse scans 4 repos in under 3 seconds. Status color signals failures before the text is read. Stalled and critical vulns are visually distinct at glance range. The reviewer table answers "who's doing work?" without drilling anywhere.

**Information per scroll-pixel.** No wasted space. No cards with padding. No charts that consume 200px to show a number that fits in 2 characters. On a 375px mobile screen this density advantage is decisive.
