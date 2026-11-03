"""OpenCode Zen free-model privacy tags.

Source page: https://opencode.ai/v2/docs/console/models/ (no ETag, so the
cached copy is reused for CACHE_TTL seconds).

The docs default rule is zero-retention + no training; the #privacy list names
the exceptions, and #free-models describes each free model in prose. Tag values:

  trains         wording says request data may improve the model, or is logged
  zdr            zero-retention and no training (provider claim only)
  zdr-retained   no training, but data is kept (e.g. 30 days)
  (no entry)     model is not mentioned on the page
"""

import re

from lib.fetch import FetchError, fetch_cached
from lib.keys import compact_key, name_head

URL = "https://opencode.ai/v2/docs/console/models/"
CACHE_NAME = "zen-privacy.html"
CACHE_TTL = 24 * 3600
TAGS = ("trains", "zdr", "zdr-retained")

_LI = re.compile(r"<li>(.*?)</li>", re.S)
_TAG = re.compile(r"<[^>]+>")


def text(value):
    """Cell/paragraph text: tags dropped, whitespace collapsed."""
    out = re.sub(r"<br\s*/?>", " ", value)
    out = _TAG.sub(" ", out)
    for entity, char in (("&gt;", ">"), ("&lt;", "<"), ("&amp;", "&"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        out = out.replace(entity, char)
    return " ".join(out.split())


key = compact_key


def name_key(value):
    """Key of the model name alone, dropping the sentence after it.

    'Big Pickle: During its free period...' -> 'bigpickle'
    'Nemotron 3 Ultra Free (NVIDIA ...): ...' -> 'nemotron3ultrafree'
    """
    return key(name_head(value))


def classify(value):
    """Wording -> tag, None when the text says nothing."""
    lowered = value.lower()
    if ("used to improve" in lowered or "improve the model" in lowered
            or "logged for security" in lowered):
        return "trains"
    if (("does not use" in lowered and "training" in lowered)
            or "not used for training" in lowered):
        if "30 days" in lowered or "retained" in lowered:
            return "zdr-retained"
        return "zdr"
    return None


def list_items(html, anchor):
    """Texts of the <li> items in the <ul> right after id="<anchor>"."""
    items = []
    at = html.find('id="%s"' % anchor)
    if at < 0:
        return items
    start = html.find("<ul>", at)
    if start < 0:
        return items
    end = html.find("</ul>", start)
    if end < 0:
        return items
    for raw in _LI.findall(html[start:end]):
        item = text(raw)
        if item:
            items.append(item)
    return items


def parse(html):
    """Page HTML -> {normalised model key: tag}."""
    exceptions = {}
    for item in list_items(html, "privacy"):
        item_key = name_key(item)
        if item_key:
            exceptions[item_key] = item

    tags = {}
    for item in list_items(html, "free-models"):
        item_key = name_key(item)
        if item_key:
            # Docs default rule covers every console model; only #privacy
            # names exceptions, so silence means zero-retention.
            tags[item_key] = classify(item + " " + exceptions.get(item_key, "")) or "zdr"
    for item_key, item in exceptions.items():
        if item_key not in tags:
            tags[item_key] = classify(item) or "zdr"
    return tags


def load(cache_dir=None, no_network=False, ttl=CACHE_TTL):
    """Fetch (or reuse) the docs page and return (payload, meta)."""
    try:
        body, meta = fetch_cached(URL, CACHE_NAME, cache_dir, no_network,
                                  max_age=None if no_network else ttl)
    except FetchError as exc:
        return ({"tags": {}, "source": {"url": URL, "from_cache": False,
                                        "error": str(exc)}}, None)
    tags = parse(body.decode("utf-8", "replace"))
    return ({"tags": tags, "source": {"url": URL, "etag": meta.get("etag"),
                                      "fetched_at": meta.get("fetched_at"),
                                      "from_cache": bool(meta.get("from_cache")),
                                      "error": meta.get("error")}}, meta)


def tag_for(tags, model_id, name=None):
    """Tag for one model, matching id first and display name second."""
    found = tags.get(key(model_id))
    if found is None and name:
        found = tags.get(key(name))
    return found
