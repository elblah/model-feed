"""Peak-pricing windows, published as data instead of being decided per run.

DeepSeek (and anything else we add later) charges a multiplier inside fixed
daily windows on fixed weekdays, in UTC. The consumer picks the applicable
price using its own clock, so a static file stays correct all day.

Reference: https://api-docs.deepseek.com/quick_start/pricing
Windows: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday only (weekends and
CN public holidays are always off-peak). Half-open intervals [start, end).
"""

MULT = 2.0
PROVIDERS = ("opencode-go",)
NAME_MATCH = ("deepseek",)
WINDOWS = (
    {"days": "Mon-Fri", "start": "01:00", "end": "04:00", "tz": "UTC"},
    {"days": "Mon-Fri", "start": "06:00", "end": "10:00", "tz": "UTC"},
)


def applies(provider_id, model):
    """True when this provider/model combination is charged peak rates."""
    if provider_id not in PROVIDERS:
        return False
    haystack = " ".join(
        str(model.get(key) or "") for key in ("id", "name", "family")
    ).lower()
    return any(token in haystack for token in NAME_MATCH)


def windows():
    return [dict(window) for window in WINDOWS]


def info():
    """Metadata block attached to a peak-priced model."""
    return {"mult": MULT, "windows": windows(), "applies_now": "consumer-computed"}
