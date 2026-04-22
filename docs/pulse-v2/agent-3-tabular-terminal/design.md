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
