"""models.dev/api.json -> one normalised catalog per feed file.

The upstream file is ~6 MB of JSON covering every provider. We keep only what
is needed to list and price a model, so a consumer downloads a few KB.

A feed file can be narrower than its upstream provider (free models only) and
can be enriched by other sources before it is written: monthly plan caps,
privacy tags, or an official /v1/models list that overrides what exists.
"""

import json

from lib import peaks
from lib import schema
from lib.fetch import FetchError, fetch_json
from lib.keys import hyphen_key
from sources import zenprivacy
URL = "https://models.dev/api.json"
CACHE_NAME = "models.dev-api.json"


def fetch_api(cache_dir=None, no_network=False):
    """Return (api_dict, meta). meta is the fetch_cached() metadata."""
    data, meta = fetch_json(URL, CACHE_NAME, cache_dir, no_network)
    if not isinstance(data, dict) or not data:
        raise FetchError("models.dev payload is not a provider map")
    return data, meta


def provider_ids(api):
    return sorted(api.keys())


def _apply_official(entries, official_ids, provider_api):
    """Endpoint list decides existence; endpoint-only ids become bare entries."""
    known = set(official_ids)
    for entry in entries:
        if entry["id"] in known:
            entry.pop("status", None)
            entry["official"] = True
        else:
            entry["status"] = "deprecated"

    present = {entry["id"] for entry in entries}
    for model_id in official_ids:
        if model_id in present:
            continue
        entries.append({
            "id": model_id,
            "name": model_id,
            "family": model_id.split("/")[0] if "/" in model_id else "",
            "context": schema.OFFICIAL_CONTEXT,
            "max_output": 0,
            "cost": schema.cost(None),
            "free": False,
            "tool_call": True,
            "reasoning": False,
            "open_weights": False,
            "release_date": "",
            "api": provider_api or "",
            "official": True,
            "bare": True,
        })


def _apply_caps(entries, caps):
    """Attach monthly plan caps (and promo/trains flags) by id or name."""
    table = caps.get("models") or {}
    if not table:
        return 0

    lookup = {}
    for model_id, item in table.items():
        lookup[model_id] = item
        lookup.setdefault(hyphen_key(model_id), item)

    hits = 0
    for entry in entries:
        item = table.get(entry["id"]) or lookup.get(hyphen_key(entry["name"]))
        if not item:
            continue
        hits += 1
        if item.get("cap_usd"):
            entry["cap_usd"] = item["cap_usd"]
        if item.get("promo"):
            entry["promo"] = item["promo"]
        if item.get("trains"):
            entry["trains"] = True
    return hits


def _apply_privacy(entries, privacy):
    tags = privacy.get("tags") or {}
    hits = 0
    for entry in entries:
        found = zenprivacy.tag_for(tags, entry["id"], entry["name"])
        if found:
            entry["privacy"] = found
            hits += 1
    return hits


def build_provider(api, provider_id, source_meta=None, feed_id=None,
                   free_only=False, caps=None, privacy=None, official=None,
                   prices=None):
    """Normalise one provider into a published feed payload."""
    raw_provider = api.get(provider_id)
    if not isinstance(raw_provider, dict):
        raise FetchError("provider %r not present in models.dev" % provider_id)
    raw_models = raw_provider.get("models")
    if not isinstance(raw_models, dict) or not raw_models:
        raise FetchError("provider %r has no models in models.dev" % provider_id)

    provider_api = raw_provider.get("api") or ""
    entries = []
    peak_models = []
    for model_id in sorted(raw_models):
        raw = dict(raw_models[model_id])
        raw.setdefault("id", model_id)
        entry = schema.model_entry(raw, provider_api)
        if free_only and not entry["free"]:
            continue
        if peaks.applies(provider_id, raw):
            # Publish both prices; the consumer applies its own clock.
            entry["cost_peak"] = schema.scaled(entry["cost"], peaks.MULT)
            entry["peak"] = peaks.info()
            peak_models.append(entry["id"])
        entries.append(entry)

    levers = {"peak": {"enabled": bool(peak_models), "models": peak_models},
              "caps": False, "privacy": False, "official": False}

    if official is not None:
        _apply_official(entries, official, provider_api)
        levers["official"] = True

    if caps and _apply_caps(entries, caps):
        levers["caps"] = {"enabled": True,
                          "plan_default": caps.get("plan_default"),
                          "plans": caps.get("plans") or []}

    if privacy and _apply_privacy(entries, privacy):
        levers["privacy"] = {"enabled": True, "tags": list(zenprivacy.TAGS)}

    entries.sort(key=lambda item: item["id"])

    meta = source_meta or {}
    return {
        "schema": schema.SCHEMA,
        "feed": feed_id or provider_id,
        "generated_at": meta.get("fetched_at"),
        "provider": {
            "id": raw_provider.get("id") or provider_id,
            "name": raw_provider.get("name") or provider_id,
            "api": provider_api,
            "docs": raw_provider.get("doc") or "",
            "env": raw_provider.get("env") or [],
            "npm": raw_provider.get("npm") or "",
        },
        "source": {
            "url": URL,
            "etag": meta.get("etag"),
            "fetched_at": meta.get("fetched_at"),
        },
        "levers": levers,
        "models": entries,
    }


def dumps(payload, pretty=False):
    """Deterministic serialisation: sorted keys, stable separators."""
    if pretty:
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    else:
        text = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    return (text + "\n").encode("utf-8")
