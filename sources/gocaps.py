"""OpenCode Go plan caps, training and retention flags.

The public docs page no longer carries the per-model cap tables (they moved
behind the console), so this source reads the console itself. The console is
server-rendered and needs no auth:

  1. https://opencode.ai/console/go            -> <script src=".../entry-client-<h>.js">
  2. .../entry-client-<h>.js                   -> dependency chunk assets/go-<h>.js
  3. .../go-<h>.js                             -> embedded catalogue literal

The chunk holds a JavaScript object literal with one object per model:

  id, name, cost{text{...}, multipliers[...]}, api{...},
  go{monthly_usage{go, go-plus, go-teams}, training, zdr, limited_regions,
     featured}

monthly_usage is the $ allowance of each plan; a model without an entry has no
cap (free / unlimited). The console Privacy column is driven by two flags: zdr
true shows "0 days" retention, false shows "Not ZDR"; training marks models
that learn from request data. limited_regions marks models served from a
subset of regions only. This single fetch after the asset resolution replaces
the old caps + privacy + endpoint tables.

Asset names are content-hashed, so the chunk name is resolved on every run
while the bodies are cached under stable names. Plan prices still live on the
docs page, which is parsed as markdown; when that fetch fails the plan list
falls back to the last known prices.

Returned payload (consumed by sources/modelsdev._apply_caps):

    {"plan_default": "go",
     "plans": [{"id", "name", "price_usd", "covers"}, ...],
     "models": {model_id: {"cap_usd": {plan: $}, "trains": True,
                           "zdr": False, "limited_regions": True,
                           "privacy": "zdr-retained"}}}
"""

import json
import re
from urllib.parse import urljoin

from lib.fetch import FetchError, fetch_cached

URL = "https://opencode.ai/console/go"
DOCS_URL = "https://opencode.ai/docs/console/go.md"
CACHE_TTL = 6 * 3600
HTML_CACHE = "go-console.html"
DOCS_CACHE = "go-console.md"
ENTRY_CACHE = "go-entry-client.js"
CHUNK_CACHE = "go-chunk.js"

PLANS = ("go", "plus", "teams")
DEFAULT_PLAN = "go"
DEFAULT_PLANS = (
    {"id": "go", "name": "Go", "price_usd": 10.0,
     "covers": "Lower-cost access to the models below"},
    {"id": "plus", "name": "Go Plus", "price_usd": 40.0,
     "covers": "Higher usage limits across the models below"},
    {"id": "teams", "name": "Go Teams", "price_usd": 20.0,
     "covers": "Every member gets their own limits, $5/month higher than Go"},
)

# console key -> published plan id
PLAN_KEYS = {"go": "go", "go-plus": "plus", "go-teams": "teams"}

_SCRIPT = re.compile(r'<script[^>]+src="([^"]*entry-client[^"]*\.js)"')
_DEP = re.compile(r"assets/(go-[A-Za-z0-9_\-]+\.js)")
_MARKER = "{object:`list`,data:["
_USD = re.compile(r"\$([0-9]+(?:\.[0-9]+)?)")
_MODEL_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


def parse_plans(markdown):
    """Docs markdown -> plan list. Rows are Plan | Price | Included usage."""
    plans = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        name = cells[0].replace("**", "").strip()
        if not name.lower().startswith("go") or len(plans) >= len(PLANS):
            continue
        price = _USD.search(cells[1].replace("**", ""))
        if not price:
            continue
        plans.append({"id": PLANS[len(plans)], "name": name,
                      "price_usd": float(price.group(1)),
                      "covers": cells[2].replace("**", "").strip()})
    return plans


def literal(chunk):
    """Slice the `[ ... ]` catalogue array out of the JS chunk."""
    start = chunk.find(_MARKER)
    if start < 0:
        raise ValueError("go chunk: catalogue literal not found")
    start += len(_MARKER) - 1
    depth = 0
    for index in range(start, len(chunk)):
        char = chunk[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return chunk[start:index + 1]
    raise ValueError("go chunk: unbalanced catalogue literal")


def js_to_json(text):
    """Best-effort JavaScript object literal -> JSON text."""
    text = text.replace("`", '"')
    text = re.sub(r"!0", "true", text)
    text = re.sub(r"!1", "false", text)
    text = re.sub(r"([{,\[]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', text)
    text = re.sub(r"([:,\[]\s*)\.(\d)", r"\g<1>0.\2", text)
    return text


def usd(value):
    """Value from monthly_usage -> float dollars, or None when unlimited."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        found = _USD.search(value)
        if found:
            return float(found.group(1))
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def privacy_tag(flags):
    """Console go flags -> zen-style privacy tag, None when unspecified.

    training        -> "trains"       (request data may improve the model)
    no training+zdr -> "zdr"          (console shows "0 days" retention)
    no training     -> "zdr-retained" (console shows "Not ZDR": data kept,
                                       but not used for training)
    """
    if not isinstance(flags, dict) or "zdr" not in flags:
        return None
    if flags.get("training"):
        return "trains"
    return "zdr" if flags.get("zdr") else "zdr-retained"


def parse(chunk, markdown=""):
    """Chunk body (+ docs markdown) -> caps payload (see module docstring)."""
    data = json.loads(js_to_json(literal(chunk)))
    if not isinstance(data, list) or not data:
        raise ValueError("go chunk: catalogue literal is empty")

    models = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or not _MODEL_ID.match(model_id):
            continue
        flags = item.get("go") or {}
        usage = flags.get("monthly_usage") or {}
        caps = {}
        for key, value in usage.items():
            plan = PLAN_KEYS.get(key)
            amount = usd(value)
            if plan and amount is not None:
                caps[plan] = amount
        entry = {}
        if caps:
            entry["cap_usd"] = caps
        if flags.get("training"):
            entry["trains"] = True
        if "zdr" in flags:
            entry["zdr"] = bool(flags.get("zdr"))
        if flags.get("limited_regions"):
            entry["limited_regions"] = True
        tag = privacy_tag(flags)
        if tag:
            entry["privacy"] = tag
        if entry:
            models[model_id] = entry

    if not models:
        raise ValueError("go chunk: no models parsed")
    if not any(item.get("cap_usd") for item in models.values()):
        # The catalogue loaded but every allowance is missing: the shape of
        # monthly_usage changed. Fail so the feed keeps its last good copy
        # instead of publishing caps:false.
        raise ValueError("go chunk: no monthly_usage caps parsed")

    plans = parse_plans(markdown) if markdown else []
    if len(plans) < len(PLANS):
        plans = [dict(plan) for plan in DEFAULT_PLANS]
    return {"plan_default": DEFAULT_PLAN, "plans": plans, "models": models}


def _chunk_body(html, cache_dir, no_network):
    """Resolve the hashed go chunk through the entry script."""
    page = html.decode("utf-8", "replace")
    found = _SCRIPT.search(page)
    if not found:
        raise FetchError("go console: entry script not found in %s" % URL)
    entry_url = urljoin(URL, found.group(1))
    entry, _meta = fetch_cached(entry_url, ENTRY_CACHE, cache_dir, no_network)

    names = sorted(set(_DEP.findall(entry.decode("utf-8", "replace"))))
    if not names:
        raise FetchError("go console: no go chunk listed in %s" % entry_url)
    errors = []
    for name in names:
        chunk_url = urljoin(entry_url, "/console/_build/assets/" + name)
        try:
            body, meta = fetch_cached(chunk_url, CHUNK_CACHE, cache_dir,
                                      no_network)
        except FetchError as exc:
            errors.append(str(exc))
            continue
        return body, meta
    raise FetchError("go console: no usable chunk (%s)" % "; ".join(errors))


def load(cache_dir=None, no_network=False):
    """Fetch the console + assets, return (payload, meta)."""
    max_age = None if no_network else CACHE_TTL
    html, meta = fetch_cached(URL, HTML_CACHE, cache_dir, no_network,
                              max_age=max_age)
    chunk, chunk_meta = _chunk_body(html, cache_dir, no_network)

    markdown = ""
    try:
        docs, _docs_meta = fetch_cached(DOCS_URL, DOCS_CACHE, cache_dir,
                                        no_network, max_age=max_age)
        markdown = docs.decode("utf-8", "replace")
    except FetchError:
        pass  # plan prices fall back to DEFAULT_PLANS

    payload = parse(chunk.decode("utf-8", "replace"), markdown)
    payload["source"] = {"url": URL, "etag": meta.get("etag"),
                         "fetched_at": meta.get("fetched_at"),
                         "from_cache": bool(meta.get("from_cache")),
                         "chunk": chunk_meta.get("url"),
                         "error": meta.get("error")}
    return payload, meta
