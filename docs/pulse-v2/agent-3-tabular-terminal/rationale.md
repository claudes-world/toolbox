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
