# Dashboard Consolidation Approach — Issue #52

**Date:** 2026-04-20
**Purpose:** How the PM subagent in #52 assembles the review packet for Liam.

---

## What #52 Produces

A single file: `~/claudes-world/tmp/20260420-dashboard-review-packet.md`

Shared with Liam via `send-md` skill.

---

## Structure of the Review Packet

**Section 1 — Comparison blurb (1-2 paragraphs, written by PM)**

PM subagent reads all 4 `design.md` and `rationale.md` files, then writes a short synthesis paragraph describing how the archetypes diverge and what tradeoff each one makes. This is editorial summary, not scoring. No rubric, no rankings.

Example framing: "Agent 1 trades visual appeal for maximum data density — useful when you know what you're looking for. Agent 4 inverts this: it assumes something is broken and leads with alerts. Agents 2 and 3 sit between these extremes..."

**Section 2 — Appendices (12 artifacts, labeled)**

All 12 files (4 agents × 3 artifacts) appended in order, grouped per agent:

```
## Agent 1 — minimalist-text
### design.md
<contents>
### dashboard.tsx
<contents>
### rationale.md
<contents>

## Agent 2 — visual-chart
...
```

---

## Liam's Role

- Read the comparison blurb first
- Open any artifact that looks interesting
- Cherry-pick the best design(s) OR use the 4 as seeds for a redesign in #53
- The orchestrator does NOT rank, score, or recommend a winner

---

## What This Approach Deliberately Omits

- No rubric or scoring grid
- No evidence-artifact contract
- No "confidence levels" or "reviewer consensus" — that is circular LLM confirmation and does not reflect how Liam actually evaluates design
- No automated selection pass before Liam sees the work
