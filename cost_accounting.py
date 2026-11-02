"""Verified current token rates and common accounting/reporting helpers.

Prices are USD / million tokens; cache writes REPLACE ordinary input charges.
No network access or historical price reconstruction is performed.
"""
import re

CHECKED_ON = "2026-09-12"
SOURCES = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "claude": "https://platform.claude.com/docs/en/about-claude/pricing",
}
# Input, cache read, cache write, output. None means no separate write tariff.
OPENAI = {
    "gpt-6-astra": (10, 1, 12.5, 50),
    "gpt-5.6-sol": (4, .4, 5, 20),
    "gpt-5.6-terra": (2, .2, 2.5, 12),
    "gpt-5.6-luna": (.2, .02, .25, 1.2),
    "gpt-5.6-cyber": (12.5, 1.25, 15.625, 75),
    "gpt-5.5": (5, .5, None, 30),
    "gpt-5.4-mini": (.75, .075, None, 4.5),
    "gpt-5.4": (2.5, .25, None, 15),
    "gpt-5": (1.25, .125, None, 10),
}
LONG_CONTEXT = {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"}
NEW_OPENAI = {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
ALIASES = {"gpt-5.6": "gpt-5.6-sol", "gpt-daybreak-blue-latest": "gpt-5.6-sol",
           "gpt-daybreak-red-latest": "gpt-5.6-cyber"}
# Input, cache read, 5m write, 1h write, output.
CLAUDE = {
    "fable-5-1": (10, .25, 12.5, 20, 50),
    "mythos-5-1": (10, .25, 12.5, 20, 50),
    "fable-5": (10, 1, 12.5, 20, 50),
    "mythos-5": (10, 1, 12.5, 20, 50),
    "opus-5": (5, .5, 6.25, 10, 25),
    **{f"opus-4-{v}": (5, .5, 6.25, 10, 25) for v in (5, 6, 7, 8)},
    "sonnet-5": (2, .2, 2.5, 4, 10),
    "sonnet-4-6": (3, .3, 3.75, 6, 15),
    "sonnet-4-5": (3, .3, 3.75, 6, 15),
    "haiku-4-5": (1, .1, 1.25, 2, 5),
}


def model_key(model, provider):
    """Accept only known IDs/aliases and date snapshots, never family substrings."""
    model = model.lower()
    if provider == "openai":
        if model in ALIASES:
            return ALIASES[model]
        return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    return re.sub(r"-\d{8}$", "", model.removeprefix("claude-"))


def integer(value):
    return type(value) is int and value >= 0


def rates_for(provider, key, total_input, metadata):
    """Return category rates, disclosed assumptions, or an unpriced reason."""
    table = OPENAI if provider == "openai" else CLAUDE
    rates = table.get(key)
    if rates is None:
        return None, [], "unknown model"
    assumptions = []
    tier = metadata.get("service_tier")
    if tier in (None, "", "auto"):
        assumptions.append("missing processing tier: assumed Standard")
        tier = "standard"
    factor = 1
    if provider == "openai":
        tier = {"default": "standard", "priority": "fast"}.get(tier, tier)
        if key == "gpt-5.6-cyber" and (total_input > 272_000 or tier != "standard"):
            return None, assumptions, "unpublished Cyber context/tier price"
        if tier == "fast":
            if key in NEW_OPENAI:
                factor = 2
            elif key == "gpt-5.5" and total_input <= 272_000:
                factor = 2.5
            elif key in ("gpt-5.4", "gpt-5.4-mini", "gpt-5") and (key != "gpt-5.4" or total_input <= 272_000):
                factor = 2
            else:
                return None, assumptions, "unpublished Fast context/model price"
        elif tier in ("batch", "flex"):
            factor = .5
        elif tier != "standard":
            return None, assumptions, "unpublished processing tier"
        rates = tuple(None if r is None else r * factor for r in rates)
        if key in LONG_CONTEXT and total_input > 272_000:
            rates = tuple(None if r is None else r * (1.5 if i == 3 else 2) for i, r in enumerate(rates))
        # Published legacy cached rates are rounded rather than exact half-rates.
        if key == "gpt-5.4" and tier in ("batch", "flex"):
            rates = (rates[0], .25 if total_input > 272_000 else .13, rates[2], rates[3])
        geo = metadata.get("inference_geo")
        if geo not in (None, "", "global", "not_available"):
            if geo not in ("us", "eu", "regional"):
                return None, assumptions, "unknown inference geography"
            if key == "gpt-6-astra" and geo == "eu" and tier == "fast":
                return None, assumptions, "Astra Fast unavailable with EU residency"
            if key == "gpt-5":
                return None, assumptions, "unverified regional price"
            rates = tuple(None if r is None else r * 1.1 for r in rates)
        elif geo != "global":
            assumptions.append("missing inference geography: assumed global")
    else:
        if tier == "batch":
            factor = .5
        elif tier != "standard":
            return None, assumptions, "unpublished processing tier"
        speed = metadata.get("speed")
        if speed is None:
            if key in ("opus-5", "opus-4-8"):
                assumptions.append("missing processing speed: assumed Standard")
            speed = "standard"
        if speed == "fast" and key == "opus-4-6":
            speed = "standard"  # API runs this older model at standard speed/rates.
        if speed == "fast":
            if key not in ("opus-5", "opus-4-8") or tier == "batch":
                return None, assumptions, "unsupported Fast model/tier"
            factor *= 2
        elif speed not in (None, "standard"):
            return None, assumptions, "unknown processing speed"
        geo = metadata.get("inference_geo")
        if geo == "us":
            if key in ("haiku-4-5", "opus-4-5", "sonnet-4-5"):
                return None, assumptions, "unsupported inference geography"
            factor *= 1.1
        elif geo not in (None, "", "not_available", "global"):
            return None, assumptions, "unknown inference geography"
        elif geo != "global":
            assumptions.append("missing inference geography: assumed global")
        rates = tuple(r * factor for r in rates)
    return rates, assumptions, None


def accounting_blank():
    return dict(cost_extra=0.0, unpriced_calls=0, invalid_calls=0, uncertain_calls=0,
                assumptions={}, issues={}, input_breakdown={})


def add_issue(agg, reason, kind="unpriced_calls"):
    agg[kind] += 1
    agg["issues"][reason] = agg["issues"].get(reason, 0) + 1


def account(agg, categories, extra=0, assumptions=()):
    agg["cost_extra"] += extra
    if extra:
        agg["uncertain_calls"] += 1
    for note in assumptions:
        agg["assumptions"][note] = agg["assumptions"].get(note, 0) + 1
    for name, (tokens, cost) in categories.items():
        bucket = agg["input_breakdown"].setdefault(name, {"tokens": 0, "cost": 0.0})
        bucket["tokens"] += tokens
        bucket["cost"] += cost


def merge_accounting(target, source):
    for key in ("cost_extra", "unpriced_calls", "invalid_calls", "uncertain_calls"):
        target[key] += source[key]
    for key in ("assumptions", "issues"):
        for name, count in source[key].items():
            target[key][name] = target[key].get(name, 0) + count
    for name, bucket in source["input_breakdown"].items():
        dest = target["input_breakdown"].setdefault(name, {"tokens": 0, "cost": 0.0})
        for key in ("tokens", "cost"):
            dest[key] += bucket[key]


def accounting_json(agg):
    unbounded = bool(agg["unpriced_calls"] or agg["invalid_calls"])
    return {
        "pricing_complete": not (unbounded or agg["uncertain_calls"] or agg["assumptions"]),
        "pricing_assumptions": dict(sorted(agg["assumptions"].items())),
        "pricing_issues": dict(sorted(agg["issues"].items())),
        "unpriced_calls": agg["unpriced_calls"], "invalid_usage_records": agg["invalid_calls"],
        "uncertain_cache_calls": agg["uncertain_calls"],
        "cost_range_usd": {"min": round(agg["cost"], 2),
                           "max": None if unbounded else round(agg["cost"] + agg["cost_extra"], 2)},
        "output_cost_usd": round(agg["cost"] - sum(v["cost"] for v in agg["input_breakdown"].values()), 6),
        "input_breakdown": {k: {"tokens": v["tokens"], "cost_usd": round(v["cost"], 6)}
                            for k, v in agg["input_breakdown"].items()},
    }


def pricing_metadata(provider):
    result = dict(pricing_checked_on=CHECKED_ON, pricing_source=SOURCES[provider],
                  pricing_basis="current API token prices; excludes hosted-tool fees and subscription charges")
    if provider == "openai":
        result["pricing_note"] = "Sol promotional rates available at least through 2026-11-21; recheck then, do not infer a replacement price."
    return result


def print_accounting(agg):
    report = accounting_json(agg)
    if not report["pricing_complete"]:
        print("Estimate incomplete; totals use the assumptions below and exclude unpriced/invalid usage.")
    for note, count in agg["assumptions"].items():
        print(f"  {note} ({count} calls)")
    for note, count in agg["issues"].items():
        print(f"  {note} ({count} records)")
    if agg["uncertain_calls"]:
        high = report["cost_range_usd"]["max"]
        upper = "unknown" if high is None else f"${high:,.2f}"
        print(f"  Cache uncertainty range under stated tier assumptions: ${agg['cost']:,.2f} to {upper}")
    print("Input cost breakdown (USD; lower estimate where cache usage is unknown):")
    for name, bucket in agg["input_breakdown"].items():
        print(f"  {name}: {bucket['tokens']:,} tokens, ${bucket['cost']:,.4f}")
