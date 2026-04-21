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
