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
