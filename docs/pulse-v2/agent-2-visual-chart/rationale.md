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
