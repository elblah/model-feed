"""Official provider /v1/models endpoints.

Some providers publish an OpenAI-compatible model list that is the truth for
what is actually served. Merging rules:

  * listed by the endpoint      -> kept, status dropped, marked official
  * models.dev only             -> status "deprecated" (hidden by clients)
  * endpoint only               -> added as a bare entry: /v1/models carries
                                   ids only, so no prices or limits

The response is cached like every other source; it serves no ETag, so the
cache is reused for CACHE_TTL seconds.
"""

from lib.fetch import FetchError, fetch_json

ENDPOINTS = {
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    "chutes": "https://llm.chutes.ai/v1/models",
    "opencode-go": "https://opencode.ai/zen/go/v1/models",
}
CACHE_TTL = 6 * 3600


def load(provider, cache_dir=None, no_network=False):
    """Return (ids, meta) for one provider, [] when unsupported/unreachable."""
    url = ENDPOINTS.get(provider)
    if not url:
        return None, None
    name = "official-%s.json" % provider
    try:
        data, meta = fetch_json(url, name, cache_dir, no_network,
                                max_age=None if no_network else CACHE_TTL)
    except FetchError as exc:
        return [], {"url": url, "error": str(exc)}
    rows = data.get("data") if isinstance(data, dict) else data
    ids = []
    for row in rows or []:
        model_id = row.get("id") if isinstance(row, dict) else row
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return ids, {"url": url, "etag": meta.get("etag"),
                 "fetched_at": meta.get("fetched_at"),
                 "from_cache": bool(meta.get("from_cache")),
                 "error": meta.get("error")}
