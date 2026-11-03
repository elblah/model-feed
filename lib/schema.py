"""Schema version and normalisation helpers shared by every source."""

SCHEMA = 1

# /v1/models only publishes ids, so bare entries get a conservative context.
OFFICIAL_CONTEXT = 100000

COST_KEYS = ("in", "out", "cache_read", "cache_write")
_COST_FROM_UPSTREAM = {
    "in": "input",
    "out": "output",
    "cache_read": "cache_read",
    "cache_write": "cache_write",
}


def num(value, default=0):
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def cost(raw):
    """Normalise a models.dev-style cost object (USD per 1M tokens)."""
    raw = raw or {}
    out = {}
    for key in COST_KEYS:
        out[key] = round(num(raw.get(_COST_FROM_UPSTREAM[key])), 6)
    return out


def scaled(cost_obj, mult):
    return {key: round(cost_obj[key] * mult, 6) for key in COST_KEYS}


def is_free(cost_obj):
    return cost_obj["in"] == 0 and cost_obj["out"] == 0


def model_entry(raw, provider_api, peak_mult=None):
    """Normalise one upstream model record into schema v1."""
    cost_obj = cost(raw.get("cost"))
    limit = raw.get("limit") or {}
    entry = {
        "id": raw.get("id"),
        "name": raw.get("name") or raw.get("id"),
        "family": raw.get("family") or "",
        "context": int(num(limit.get("context"))),
        "max_output": int(num(limit.get("output"))),
        "cost": cost_obj,
        "free": is_free(cost_obj),
        "tool_call": bool(raw.get("tool_call")),
        "reasoning": bool(raw.get("reasoning")),
        "open_weights": bool(raw.get("open_weights")),
        "release_date": raw.get("release_date") or "",
        "api": (raw.get("provider") or {}).get("api") or provider_api or "",
    }
    upstream_id = raw.get("canonical_model_id")
    if upstream_id:
        entry["upstream_id"] = upstream_id
    # Optional keys are only emitted when set, to keep payloads small.
    for key in ("status", "official", "bare", "cap_usd", "promo", "trains",
                "privacy"):
        if raw.get(key) is not None:
            entry[key] = raw[key]
    return entry
