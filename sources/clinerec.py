"""Cline recommended-models roster (the list the Cline UI actually offers).

The docs table lists every model that exists; this endpoint lists the subset
currently offered for ClinePass, in display order. Publishing it means the
client does not have to fetch the endpoint on every run. The roster is only a
hint: when the endpoint is unavailable the feed still builds, without it.
"""

import json

from lib.fetch import FetchError, fetch_cached

URL = "https://api.cline.bot/api/v1/ai/cline/recommended-models"
CACHE_NAME = "cline-rec.json"


def _ids(items):
    out = []
    for item in items or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
    return out


def parse(text):
    """Return {"roster": [...], "free": [...]} from the endpoint body."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise FetchError("recommended-models payload is not an object")
    return {"roster": _ids(data.get("clinePass")),
            "free": _ids(data.get("free"))}


def load(cache_dir=None, no_network=False):
    """Fetch and parse the roster. Returns (roster, meta)."""
    body, meta = fetch_cached(URL, CACHE_NAME, cache_dir, no_network)
    roster = parse(body.decode("utf-8", "replace"))
    if not roster["roster"]:
        raise FetchError("recommended-models returned an empty roster")
    return roster, meta
