"""docs.cline.bot/clinepass.md -> ClinePass reference prices.

models.dev carries a cline-pass block with machine-readable costs, but it lags
the pricing docs (it missed a DeepSeek hike, a GLM release, a Qwen tier). The
docs page is the price truth, so this source parses its "Models" and "Reference
pricing" tables and the prices are joined onto the models.dev catalog by id.

Variant rows collapse onto one id: the plain row wins, then "(Off-peak)" (peak
is a 2x multiplier handled by the consumer, same convention as the other
feeds), then a "<=256K" tier row. Rows naming a model the docs id table does
not map are skipped rather than guessed at.

Ids the docs price but models.dev does not list are returned separately so the
build can publish them as unlisted entries instead of dropping real models.
"""

import re

from lib import schema
from lib.fetch import FetchError, fetch_cached

URL = "https://docs.cline.bot/getting-started/clinepass.md"
CACHE_NAME = "clinepass-docs.md"

_MODEL_SECTION = "## Models"
_PRICE_SECTION = "## Reference pricing"


def _cells(line):
    return [cell.strip().replace("\\", "") for cell in line.strip().strip("|").split("|")]


def _is_header(name):
    return name.lower() == "model" or set(name) <= {"-", ":", " "}


def _money(text):
    text = text.strip().replace("\\", "").replace("$", "").replace(",", "")
    if text in ("-", ""):
        return None
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", text)  # first rate wins: "2.00 (5min) / 4.00 (1h)"
    return float(match.group(0)) if match else None


def _id_table(md):
    """Display name -> model id, from the 'Models' section."""
    if _MODEL_SECTION not in md:
        return {}
    section = md.split(_MODEL_SECTION, 1)[1].split("\n## ", 1)[0]
    table = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 2 or _is_header(cells[0]):
            continue
        table[cells[0]] = cells[1].strip("`")
    return table


def _rank(qualifier):
    """Lower beats higher: base row, off-peak, small-context tier, the rest."""
    if qualifier == "":
        return 0
    if qualifier.lower() == "off-peak":
        return 1
    if qualifier.startswith("\u2264") or qualifier.startswith("<="):
        return 2
    return 3


def parse(md):
    """Markdown -> {model_id: {model, input, output, cache_read, cache_write}}."""
    ids = _id_table(md)
    if _PRICE_SECTION not in md:
        raise FetchError("clinepass docs: section 'Reference pricing' missing")
    section = md.split(_PRICE_SECTION, 1)[1].split("\n## ", 1)[0]

    best = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 5 or _is_header(cells[0]):
            continue
        name = re.sub(r"<sup>.*?</sup>", "", cells[0])  # footnote markup
        name = re.sub(r"(?<=\))\d+$", "", name)         # footnote digit
        match = re.search(r"\s*\(([^)]*)\)\s*$", name)  # variant qualifier
        qualifier, base = (match.group(1), name[:match.start()]) if match else ("", name)
        model_id = ids.get(base)
        if not model_id:
            continue
        rank = _rank(qualifier)
        if model_id in best and best[model_id][0] <= rank:
            continue
        best[model_id] = (rank, {
            "model": name,
            "input": _money(cells[1]),
            "output": _money(cells[2]),
            "cache_read": _money(cells[3]),
            "cache_write": _money(cells[4]),
        })
    return {model_id: row for model_id, (_rank_, row) in best.items()}


def load(cache_dir=None, no_network=False):
    """Fetch and parse the docs page. Returns (prices, meta)."""
    body, meta = fetch_cached(URL, CACHE_NAME, cache_dir, no_network)
    prices = parse(body.decode("utf-8", "replace"))
    if not prices:
        raise FetchError("clinepass docs produced no prices (layout changed?)")
    return prices, meta


def apply(entries, prices):
    """Override each entry's cost with the docs price for the same id.

    Returns (priced, unpriced) id lists; unpriced entries keep the models.dev
    cost and are flagged so a consumer can tell the two apart.
    """
    priced, unpriced = [], []
    for entry in entries:
        row = prices.get(entry["id"])
        if not row:
            entry["price_source"] = "modelsdev"
            unpriced.append(entry["id"])
            continue
        entry["cost"] = schema.cost(row)
        entry["free"] = schema.is_free(entry["cost"])
        entry["price_source"] = "docs"
        priced.append(entry["id"])
    return priced, unpriced


# ClinePass is an allowance plan, not a per-model meter: a fixed monthly fee
# buys a request budget whose size depends on the plan tier multiplier. The
# consumer turns "requests per month" into the usable range low..high.
ALLOWANCE = {
    "style": "allowance",
    "plan_default": 9.99,
    "mult_min": 2.0,
    "mult_max": 5.0,
    "unit": "req/month",
}


def allowance():
    """Plan metadata published as a lever so the client need not guess."""
    return dict(ALLOWANCE)


def docs_entries(prices, published_ids):
    """Docs-priced ids absent from models.dev, as full schema entries.

    ClinePass is the only place these models are documented, so dropping them
    would silently shrink the catalog the client can offer.
    """
    known = set(published_ids)
    entries = []
    for model_id in sorted(prices):
        if model_id in known:
            continue
        row = prices[model_id]
        cost_obj = schema.cost(row)
        entries.append({
            "id": model_id,
            "name": row["model"],
            "family": "",
            "context": 0,
            "max_output": 0,
            "cost": cost_obj,
            "free": schema.is_free(cost_obj),
            "tool_call": True,
            "reasoning": True,
            "open_weights": False,
            "release_date": "",
            "api": "",
            "price_source": "docs",
            "docs_only": True,
        })
    return entries
