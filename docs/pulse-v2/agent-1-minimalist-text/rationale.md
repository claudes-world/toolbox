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
