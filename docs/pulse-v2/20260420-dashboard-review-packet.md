# org-pulse v2 — Dashboard Design Proposals for Review

- **What pulse is:** A GitHub org health monitor that captures PRs, issues, vulnerabilities, and reviewer activity across all repos in a single snapshot.
- **What v2 delivers:** A live React dashboard embedded in the Telegram mini app, replacing the Markdown digest with an interactive, data-bound UI.
- **How to use this doc:** Four parallel agents each built a distinct archetype from the same fixture data. Read the comparison table, skim the appendices at whatever depth you want, and pick the design (or combination of ideas) you want to build.

---

## Side-by-side comparison

| # | Archetype | Core concept | Unique strength | Trade-off |
|---|-----------|--------------|-----------------|-----------|
| 1 | minimalist-text | Monospace terminal grid, every repo on one screen | All 4 repos visible before first scroll | Low discoverability; tap-to-expand not obvious |
| 2 | visual-chart | Donut + bar charts above repo cards | Pre-interprets org health visually in < 1 sec | Lower data density; more vertical space per repo |
| 3 | tabular-terminal | Sortable, filterable 9-column table | User controls sort; scales to 40+ repos easily | Horizontal scroll required on 375px viewport |
| 4 | alert-first | Ranked alert list, healthy repos collapsed | Zero-reading answer to "anything on fire?" | Global context (healthy baseline) hidden by default |

---

## My pick — orchestrator reading lens

Here's one way to look at it. For a mobile-first Telegram mini app where the dominant use pattern is a 30-second morning glance, Agent 4 (alert-first) has the tightest fit: the first thing on screen is the worst problem, and an empty alert list is its own answer. Agent 1 (minimalist-text) is a close second — it shows the whole org at once on 375px without a single scroll, which is a real advantage when you just need a fast status sweep. Agent 2's charts are compelling for weekly reviews but add interpretation overhead for daily triage; Agent 3's sortable table shines once the org grows past ~10 repos. That said, this is Liam's call — the designs are the inputs.

---

## Appendix 1 — Agent 1 (minimalist-text)

**TSX:** 598 lines · **Output dir:** ~/claudes-world/tmp/20260420-dashboard-agent-1/

**→ [View dashboard.tsx on GitHub](https://github.com/claudes-world/toolbox/blob/dev-phase-3-pulse/docs/pulse-v2/agent-1-minimalist-text/dashboard.tsx)**

### design.md

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

### rationale.md

# Rationale — minimalist-text is best for pulse users

## The audience

Two distinct use cases, one dashboard. Liam does a morning check — 30 seconds, "anything broken?" Agents (pm-dobot, gstack-dobot) check before opening PRs or filing issues — 5 seconds, "is the repo healthy?". Both cases are triage, not analysis. The dashboard is not a BI tool.

## Why density wins on mobile

The Telegram mini app viewport is 375px and the user is frequently glancing mid-conversation. A chart-forward or card-based design requires scrolling to see more than 2–3 repos. The minimalist-text layout shows all 4 repos without a single scroll event. Status, PR count, vuln severity, and upstream drift are visible simultaneously. On a 12px monospace grid, the entire org health fits in ~200px of vertical space before any expansion.

Charts add visual processing overhead that benefits analysis (trend direction, distribution shape) but adds nothing to triage (is this number bad or good?). A CRITICAL vuln count of 1 in red communicates faster than a severity bar chart.

## What alerts pop without any interaction

- Red rows catch failed captures at a glance — the red `FAIL` prefix is the first column, leftmost scan position
- Stalled PRs surface as `PR:1!1` — the `!N` suffix is the signal, not buried inside a modal
- `scope?` in yellow for null vulnerability_alerts is immediately legible without clicking anything
- The warm-up banner occupies the very top of the viewport — impossible to miss

## Why agents specifically benefit

Agents scanning health before committing work need boolean answers: "is this repo safe to push to?" The text layout returns those answers in the summary row without requiring tap-to-expand. An agent that can read the fixture JSON can also read a text table — the cognitive model is aligned.

## The tradeoff accepted

Low discoverability for drill-down context. A first-time user doesn't know rows are tappable. Mitigation: the expand indicator (`▸`) and the footer hint "tap row to expand" address this without adding graphical chrome. Accepted as a fair tradeoff for a focused internal tool with a small, consistent user base.

## Against the alternatives

Chart dashboards optimize for stakeholder demos and trend analysis — neither applies here. Sortable tables (Agent 3) optimize for comparison across repos, useful when managing dozens of repos, marginal for 4. Alert-first (Agent 4) inverts hierarchy usefully for on-call tools but removes global context (reviewer activity, repo health at a glance). minimalist-text is the only archetype that serves both the 5-second agent check and the 30-second morning review without mode-switching.

---

## Appendix 2 — Agent 2 (visual-chart)

**TSX:** 1073 lines · **Output dir:** ~/claudes-world/tmp/20260420-dashboard-agent-2/

**→ [View dashboard.tsx on GitHub](https://github.com/claudes-world/toolbox/blob/dev-phase-3-pulse/docs/pulse-v2/agent-2-visual-chart/dashboard.tsx)**

### design.md

# Org-Pulse v2 — Visual-Chart Design (Agent 2)

## Core Philosophy

Visual triage in 5 seconds. The dashboard leads with charts that compress the entire org's health into a single glance. A user waking up to their morning review should see the health picture before reading a single word.

## Information Hierarchy

**Tier 1 — Capture header strip** (always visible, non-scrolling context):
Snapshot timestamp, overall capture status pill (success / partial / failed), warm-up banner when active. This anchors the user temporally and flags data reliability immediately.

**Tier 2 — Summary charts (above the fold):**
1. **Vulnerability severity donut** — total vulns across the org broken out by CRITICAL / HIGH / MODERATE / LOW. Color is the encoding: red / orange / yellow / green. Scope-missing repos get a grey slice labeled "unknown". Tap a slice to scroll to affected repos.
2. **PR health horizontal bar** — org-wide PR counts split into: Draft / Stalled / Bot (dependabot/renovate) / Active. Stalled is amber, Draft is muted blue, Bot is grey, Active is green. Gives an instant sense of pipeline health.
3. **Reviewer activity bar chart** — horizontal bars per reviewer bucket (copilot, gemini-ca, claude-subagent, human:liam, human:alice), stacked by approved / change_requested / commented. This is the only view that reveals who's carrying review load.

**Tier 3 — Repo cards (below charts):**
Each repo is a card, sorted by severity: failed > has-CRITICAL-vuln > has-stalled-PR > partial > healthy. Cards show: repo name + org badge, capture status chip, PR count (with stalled/draft breakdown), vuln severity badges (CRITICAL count, HIGH count), and a link icon for forks showing upstream drift. Healthy repos with nothing to action are visually subdued.

## Interaction Model

- **Tap donut slice** → scrolls to / highlights repo cards matching that severity
- **Tap repo card** → inline expand revealing PR list, issue list, vuln details
- **PR items** in expand show draft badge, stalled amber badge, bot icon (dependabot/renovate)
- **Scope-missing badge** on vuln section within card — never shows "0 vulns"
- **Failed repo card** — red border, grey body, error summary from field_statuses, no data rows

## Mobile-First Decisions

- **375px base** — charts sized to fill 90vw, text labels truncated with ellipsis
- **Horizontal bar chart** preferred over vertical bars: labels render left-aligned at natural reading width on narrow viewports; vertical bar charts require angled or truncated labels
- **Donut chart** rather than pie: center label shows total count, more legible on small screens
- **Cards stack vertically** — no grid columns below 480px; two-column grid at 600px+
- **Safe-area padding** applied to top container via CSS variable
- **Touch targets min 44px** on all interactive elements (card headers, expand chevrons)
- Chart legend positioned below chart, not to the right, to avoid squeezing chart width

## What This Design Uniquely Optimizes For

Speed of recognition over completeness. A user doesn't need to parse rows to know "there's a CRITICAL vuln in CPC and a capture failure in meridian-fx" — the donut and card sort make that visible before reading begins. The reviewer chart surfaces team dynamics (bot vs. human review balance) that are invisible in text-only views. The tradeoff is explicit: less raw data per screenful than Agent 1 or Agent 3, but the information that appears is pre-interpreted.

### rationale.md

# Why Visual-Chart Is Best for Pulse Users

## The Core Argument

Org-pulse serves two users in very different cognitive states. An autonomous agent checking overnight results needs fast signal extraction without sustained reading attention. Liam doing a morning review has 30–60 seconds before deciding whether to dig deeper. Both users are better served by a visual-first layout than any text-heavy alternative.

Text dashboards (Agents 1 and 3) front-load the cognitive task: the user must parse rows, find the relevant numbers, and synthesize the picture themselves. The visual-chart design does the synthesis for them. The donut chart is not decoration — it encodes "how bad is the vuln situation across the whole org" as a single shape, readable in under a second. The stacked reviewer bar chart answers "who is pulling review load" without reading a single name until you want to.

## Triage Speed Is the Real Metric

For a dev team running autonomous agents 24/7, the dashboard check is a status poll, not a deep review. The visual-chart design assumes the common case is "nothing critical — confirm and move on" and the rare case is "something is red — drill in." The donut and PR health bar make the common case require zero reading. The repo cards, sorted by severity, make the rare case require one tap.

No other archetype achieves this. Tabular (Agent 3) requires column scanning. Alert-first (Agent 4) hides the healthy baseline and gives no sense of proportion. Text-only (Agent 1) is fast but only if you already know what to look for.

## The Reviewer Chart Uniquely Surfaces Team Dynamics

A key insight that text-only approaches bury: the balance between human and AI review is meaningful operational data. If bot reviewers are handling 80% of reviews, that's a signal worth seeing immediately. The horizontal stacked bar chart exposes this in a single glance. No other archetype requires this view — and without it, the reviewer data is either a table of numbers or absent entirely.

## Mobile-First Justification

Horizontal bar charts over vertical bars is a deliberate mobile call. At 375px, vertical bars either require label rotation (unreadable) or label truncation (uninformative). Horizontal bars give labels full natural width and scale without reformatting. The donut's center-label pattern keeps the chart compact while preserving the count readout. Every chart choice here was made assuming iPhone SE viewport first.

## The Tradeoff Is Worth It

Lower information density per screenful is the explicit tradeoff. Users who need to see every metric for every repo on one screen should use Agent 1 or 3. Users who need to answer "is anything on fire right now?" in 5 seconds should use this one. For a Telegram Mini App used in async mobile check-ins, the latter is the dominant use case.

---

## Appendix 3 — Agent 3 (tabular-terminal)

**TSX:** 835 lines · **Output dir:** ~/claudes-world/tmp/20260420-dashboard-agent-3/

**→ [View dashboard.tsx on GitHub](https://github.com/claudes-world/toolbox/blob/dev-phase-3-pulse/docs/pulse-v2/agent-3-tabular-terminal/dashboard.tsx)**

### design.md

# Tabular-Terminal Dashboard — Design Document

## Information Hierarchy

The tabular-terminal archetype treats repos as database rows. Every metric column is a first-class citizen with equal visual weight — the user brings their own priority through sorting. The hierarchy is:

1. **Warm-up banner** (conditional): amber strip above table when `warm_up_active: true`. Tells the user trend data is unavailable and when it fills. One line, dismissible.
2. **Filter/sort bar**: text input + capture_status pill filter. Always visible. Zero scroll required to reach it.
3. **Primary table**: one row per repo. Columns left-to-right by actionability — repo name (fixed), capture status, open PRs, stalled PRs, CRIT/HIGH/MOD/LOW vuln counts, oldest PR idle hours.
4. **Inline expansion**: tap any row to expand a sub-table showing PRs, issues, and vulns for that repo. Expansion pushes rows down; it does not modal-overlay.

No summary cards above the table. No charts. The table IS the summary.

## Interaction Model

**Sort:** tap any column header to sort ascending; tap again to sort descending. An arrow indicator shows active sort column and direction. Default sort is capture_status (failed first, then partial, then success) so problems surface without the user doing anything.

**Filter:** text box filters repo name substring (case-insensitive). Capture status pills (`ALL / SUCCESS / PARTIAL / FAILED`) are tap-exclusive — one active at a time. Both filters compose.

**Row expansion:** tap any data row to toggle inline detail. Only one row expands at a time (opening a second closes the first). Expanded section shows three sub-sections — PRs, Issues, Vulns — with their own stalled/draft indicators.

**Horizontal scroll:** on 375px viewport the table scrolls horizontally. The repo name column is `position: sticky; left: 0` so it never scrolls out of view. Column widths are fixed-px, not fluid, so numbers stay aligned.

## Mobile-First Decisions

- **Sticky first column** is the single most important mobile adaptation. Without it, the user loses repo context while scrolling right.
- **Compact row height** (36px) maximizes visible rows without pagination. Four repos fit above the fold on iPhone SE even with the filter bar.
- **Tap targets on headers** are `min-height: 44px` (Apple HIG minimum) to prevent mis-taps on small screens.
- **Font size**: 12px table body, 11px secondary labels. Tight but legible in Telegram WebKit which doesn't enforce text zoom.
- **No horizontal padding waste**: 8px cell padding. Every pixel goes to data.
- **Expanded detail** uses a card inset — indented 8px, different background — visually grouped without a modal that would fight the Telegram chrome.

## What This Design Uniquely Optimizes For

**Power-user morning triage in under 10 seconds.** The default sort surfaces broken repos at the top. A user who knows what to look for can scan the status and vuln columns without reading any prose. Sorting by "oldest stalled PR" takes one tap.

**Investigative drilling**: the inline expansion gives full PR/issue/vuln detail without leaving the table context. The user can keep the table visible above the expansion and correlate across repos.

**Data fidelity over presentation**: null vulnerability data shows "scope?" not "0" — the table never lies by omission. Failed repos get a FAILED badge and N/A in every metric column rather than empty cells that look like zeros.

This design is best for users who treat the dashboard as an operations console, not a presentation layer.

### rationale.md

# Why Tabular-Terminal Wins for Pulse Users

## The core audience problem

Pulse has two user modes: **morning review** (Liam scanning the org health in 60 seconds) and **incident investigation** (an agent or Liam drilling into a specific repo after an alert fires). These modes have opposite information needs — breadth-first scan vs. depth-first drill. Most dashboard archetypes optimize for one and compromise the other. The tabular-terminal handles both in the same UI surface.

## Why a table beats cards and charts for this data shape

The fixture has exactly 4 repos. That number will grow. An org with 20 repos degrades chart readability catastrophically — sparklines collapse to noise, card grids require scrolling past unrelated repos to find the broken one. A sortable table scales linearly: 4 repos or 40 repos, the highest-severity row is always at the top after one tap.

Charts encode one dimension well. The pulse data is multi-dimensional per repo: capture status, PR count, stalled PR count, four vuln severity levels, idle hours. Encoding all of that as bars or sparklines requires either many charts (cognitive load) or lossy aggregation (you miss the CRIT vuln because it was folded into a "health score"). A table column per metric lets the user bring their own mental query — "sort by CRIT descending, ignore repos with no PRs" — without the dashboard designer predicting that query in advance.

## Why sortable columns matter more than summary cards

A summary card at the top saying "3 total critical vulns" tells you there's a fire but not where to aim. Sorting the CRIT column descending finds the burning repo in one tap. The table is the summary — it just defaults to a useful sort order (failed first) so even a user who never sorts sees the right thing first.

## Why this is better than minimalist-text for the same audience

Agent 1 (minimalist-text) renders all repos in a fixed order with no sort or filter. That's fine when there are 4 repos and you've memorized the list. At 15+ repos it becomes a grep problem. The tabular-terminal adds sort and filter at the cost of a slightly heavier component — a worthwhile tradeoff that compounds in value as the org grows.

## Mobile-specific argument

The fixed-first-column pattern is the standard solution to wide tables on narrow screens — used by every serious data tool from pgAdmin to GitHub's PR list to Google Sheets on mobile. Horizontal scroll is not a cop-out; it preserves column alignment and lets the user see raw numbers in proper monospace columns rather than truncated text in squeezed cards.

## The killer feature: default sort

The table opens sorted by capture_status with failed first. No configuration, no morning habit required. The broken repo is row 1 every time. That alone makes this archetype the best fit for a health-monitoring use case.

---

## Appendix 4 — Agent 4 (alert-first)

**TSX:** 683 lines · **Output dir:** ~/claudes-world/tmp/20260420-dashboard-agent-4/

**→ [View dashboard.tsx on GitHub](https://github.com/claudes-world/toolbox/blob/dev-phase-3-pulse/docs/pulse-v2/agent-4-alert-first/dashboard.tsx)**

### design.md

# Alert-First Dashboard — Design Document

## Core Philosophy

This design inverts the conventional dashboard hierarchy. Instead of asking "how is everything?" and showing a summary at the top, it asks "what needs my attention right now?" and answers immediately. The first pixel visible on load is an alert. There is no summary header, no health score, no chart to interpret — just an ordered list of things that are broken.

The mental model is PagerDuty on a quiet night: if the page is empty, everything is fine. If there are items, they are ranked and actionable.

## Information Hierarchy

Alerts are sorted by a strict severity priority:

1. **Capture failures** — a repo where `capture_status: "failed"` means the snapshot is blind. No data is worse than bad data. These surface first, always.
2. **CRITICAL vulnerabilities** — known exploitable CVEs with age. A 42-day-old CRITICAL is a fire.
3. **Stalled PRs** (`hours_idle > 72`) — human-authored PRs (excluding bots) blocking merge. Dependabot/renovate PRs are excluded from stall alerts; they auto-manage.
4. **HIGH vulnerabilities** — significant risk, not yet CRITICAL urgency.
5. **Partial captures / scope-missing fields** — auth gap (e.g. `security_events` scope missing) means blind spots in the data.
6. **MODERATE/LOW vulnerabilities** — acknowledged but lower burn rate.

Each alert card shows: repo name, alert type badge, the specific artifact (GHSA ID, PR number, field name), and age/idle time. One card = one actionable item.

## Interaction Model

- **Alert expand:** tapping an alert card expands an inline detail panel with full context (error note, GHSA description, PR title). No modals — expansion is in-place, preserving scroll position.
- **Healthy section toggle:** a single collapsed row at the bottom reads "N repos healthy — show". Tapping reveals a compact list of healthy repos (name, capture status, PR count). No detail beyond that — healthy means nothing to do.
- **No navigation tabs, no side panels.** Single scrollable surface. Telegram WebKit has limited viewport; modal stacks and tab bars eat precious vertical space.

## Mobile-First Decisions

- **375px minimum width** (iPhone SE baseline). All cards are full-width, no grid layout.
- Alert cards use left-border color coding (red/yellow/orange) rather than background fills. Colored backgrounds reduce legibility in Telegram's dark mode; left-border survives both light and dark themes.
- Touch targets on expand toggles are minimum 44px tall per iOS HIG.
- The `--tg-content-safe-area-inset-top` padding prevents content from hiding under the Telegram header notch.
- Font sizes: 14px body, 12px metadata. Dense but readable on retina displays.

## What This Design Uniquely Optimizes For

**Morning triage speed.** A developer or PM opening this at 9am wants to know: is anything on fire? This design answers that in under 2 seconds — the first card visible on load is the worst problem. They scroll until the alerts stop. Then they see "N repos healthy" and close the app.

It uniquely handles the **warm-up period** honestly: a subtle footer note states that trend comparison is unavailable until April 27, rather than showing empty sparklines or misleading zeroes. This prevents false confidence.

The design sacrifices discoverability (you cannot browse healthy repos easily) in exchange for signal-to-noise compression. If you want to browse, use a different archetype. If you want to know what's broken, this is the right tool.

### rationale.md

# Why Alert-First Is the Right Archetype for Pulse Users

## The Actual Use Pattern

Org-pulse is not a metrics exploration tool. Nobody opens it to browse healthy repos or admire charts. It gets opened in two scenarios:

1. **Morning check** — Liam (or an agent) wakes up and wants to know: is anything on fire before I start work?
2. **Incident response** — something broke, the question is "which repo, what field, what error?"

Both scenarios have the same shape: the user has a question ("is anything wrong?") and needs an answer in under 5 seconds. That is what alert-first optimizes for. Every other archetype asks the user to scan and interpret before they can answer that question. Alert-first answers it immediately: the first card visible on load is the highest-severity problem.

## Why the Others Fall Short for This Use Case

The minimalist-text archetype (Agent 1) shows all repos and forces the user to scan every row looking for red cells. Information is present but not prioritized — the signal is buried in the noise.

The visual-chart archetype (Agent 2) leads with summary visualizations. Beautiful for weekly reviews; adds 3-5 seconds of interpretation overhead for morning triage. Charts answer "how is the distribution?" not "what do I fix first?"

The tabular archetype (Agent 3) is powerful for filtering and sorting but assumes the user knows they want to sort by stalled PRs or failure status. Alert-first does that sorting automatically and commits to a single ranked view — no configuration required.

## Why Alert-First Works for Both Audiences

**For agents checking dashboards:** Agents running on a schedule want a deterministic answer to "are there alerts?" If the alert list is empty, return success. If not, enumerate the items in priority order and act on them. Alert-first maps directly to an agent's decision loop — no parsing of charts or tables required.

**For Liam doing morning review:** The cognitive load at 9am should be zero. Alert-first means Liam scrolls until alerts stop. If the list is empty, the environment is healthy — close the app. If there are items, they are already ranked and each card contains enough context to act (which repo, GHSA ID, age, PR number) without navigating away.

## The Healthy Section Design Choice

Hiding healthy repos by default is not data suppression — it is appropriate triage. In a healthy system, those repos require no action. Showing them alongside alerts would dilute the signal and add scroll distance between the user and their actionable items. The "N repos healthy — show" toggle preserves access without making it the default view.

## Honest Handling of Scope Gaps

The fixture includes a repo where `vulnerability_alerts: null` because the token lacks `security_events` scope. The alert-first design surfaces this explicitly as a "Scope missing" alert — not as "0 vulnerabilities." This distinction matters: unknown state is not the same as clean state, and the dashboard treats it accordingly.
