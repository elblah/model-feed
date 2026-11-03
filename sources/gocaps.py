"""OpenCode Go plan caps, promos and privacy flags.

Source page: https://opencode.ai/v2/docs/console/go/ (plain HTML tables, no
ETag). One fetch yields everything:

  * caps tables      "monthly limit" header: plan A, then plan B ($ per model)
  * privacy table    "model training" header: which endpoints train on data
  * endpoints table  "model id" header: display name -> real model id

Models are joined on a normalised display name, so peak/off-peak and
context-tier rows of the same model collapse onto one key (their caps match,
so the shared value is unambiguous). Struck-through prices are ignored by
taking the last "$" amount in the cell.
"""

import re

from lib.fetch import fetch_cached
from lib.keys import hyphen_key, strip_parens

URL = "https://opencode.ai/v2/docs/console/go/"
CACHE_NAME = "go-caps.html"
CACHE_TTL = 6 * 3600
PLANS = ("go", "plus", "teams")
DEFAULT_PLAN = "go"

_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<(t[hd])[^>]*>(.*?)</\1>", re.S)
_USD = re.compile(r"\$([0-9.]+)")
_SMALL = re.compile(r"<small>(.*?)</small>", re.S)
_MODEL_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


def text(cell):
    """Strip tags/entities from one table cell; <br> becomes a space."""
    out = re.sub(r"<br\s*/?>", " ", cell)
    out = re.sub(r"<[^>]+>", " ", out)
    for entity, char in (("&gt;", ">"), ("&lt;", "<"), ("&amp;", "&"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        out = out.replace(entity, char)
    return " ".join(out.split())


name_key = hyphen_key


def slug(value):
    """Fallback id: 'GLM-5.3-Flash' -> 'glm-5.3-flash'."""
    value = strip_parens(value.replace("*", "").replace("`", ""))
    return re.sub(r"\s+", "-", value.strip()).lower()


def _tables(html):
    for body in _TABLE.findall(html):
        rows = []
        for row in _ROW.findall(body):
            rows.append([m.group(2).strip() for m in _CELL.finditer(row)])
        if rows:
            yield rows


def parse(html):
    """Page HTML -> {"plan_default", "models"} (see module docstring)."""
    caps_tables = []
    trains_keys = set()
    name_to_id = {}
    plans = []

    for rows in _tables(html):
        header = "|".join(text(cell) for cell in rows[0]).lower()
        if header.startswith("plan|price"):
            # Plan | Price | Included usage -- one row per plan, in the same
            # order as the monthly-limit tables further down the page.
            for index, cells in enumerate(rows[1:]):
                if len(cells) != 3 or index >= len(PLANS):
                    continue
                price = _USD.search(text(cells[1]))
                plans.append({"id": PLANS[index], "name": text(cells[0]),
                              "price_usd": float(price.group(1)) if price else None,
                              "covers": text(cells[2])})
        elif "monthly limit" in header:
            caps_tables.append(rows)
        elif "model training" in header:
            for cells in rows[1:]:
                if len(cells) == 3 and text(cells[1]).lower() == "yes":
                    key = name_key(text(cells[0]))
                    if key:
                        trains_keys.add(key)
        elif "model id" in header:
            for cells in rows[1:]:
                if len(cells) != 4:
                    continue
                model_id = re.sub(r"\s+", "", text(cells[1])).lower()
                key = name_key(text(cells[0]))
                if key and _MODEL_ID.match(model_id):
                    name_to_id[key] = model_id

    models = {}

    def entry(model_id):
        return models.setdefault(model_id, {})

    for index, plan in enumerate(PLANS):
        if index >= len(caps_tables):
            break
        for cells in caps_tables[index][1:]:
            if len(cells) != 6:
                continue
            cell = cells[5]
            promo = None
            found = _SMALL.search(cell)
            if found:
                promo = text(found.group(1)) or None
            amounts = _USD.findall(text(cell).replace("~", ""))
            if not amounts:
                continue  # "Unlimited" promo rows carry no money
            name = text(cells[0])
            key = name_key(name)
            if not key:
                continue
            model_id = name_to_id.get(key) or slug(name)
            if not model_id:
                continue
            item = entry(model_id)
            caps = item.setdefault("cap_usd", {})
            if plan not in caps:
                caps[plan] = float(amounts[-1])
            if promo:
                item.setdefault("promo", {}).setdefault(plan, promo)

    for key in trains_keys:
        # Prefer the real endpoint id; fall back to the normalised name when
        # the endpoints table has no row for it.
        entry(name_to_id.get(key) or key)["trains"] = True

    if not plans:
        plans = [{"id": pid, "name": pid.title(), "price_usd": None, "covers": None}
                 for pid in PLANS]

    return {"plan_default": DEFAULT_PLAN, "plans": plans, "models": models}


def load(cache_dir=None, no_network=False):
    """Fetch (or reuse) the docs page and return (payload, meta)."""
    body, meta = fetch_cached(URL, CACHE_NAME, cache_dir, no_network,
                              max_age=None if no_network else CACHE_TTL)
    payload = parse(body.decode("utf-8", "replace"))
    payload["source"] = {"url": URL, "etag": meta.get("etag"),
                         "fetched_at": meta.get("fetched_at"),
                         "from_cache": bool(meta.get("from_cache")),
                         "error": meta.get("error")}
    return payload, meta
