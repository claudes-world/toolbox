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
