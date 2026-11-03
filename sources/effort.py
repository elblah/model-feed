"""Resolved reasoning-effort ladders for providers whose own block is thin.

models.dev often knows a model's effort ladder only under another provider
(openrouter, opencode-go) while the provider block we publish has none. The
lookup order is published as data so a client never has to replay it against
the full 6 MB api.json.

Chain (first non-empty wins), keyed by the published id:
  1. own provider block, exact id
  2. openrouter, exact id
  3. openrouter, id suffix "/<base>"
  4. openrouter, id suffix "/<notag>"   (base without a ":variant" tag)
  5. opencode-go, bare base
  6. opencode-go, bare notag
"""

FALLBACK_PROVIDERS = ("openrouter", "opencode-go")
DEFAULT_POLICY = "xhigh-else-highest"


def _efforts(block, model_id):
    model = (block or {}).get(model_id)
    if not isinstance(model, dict):
        return []
    for option in model.get("reasoning_options") or []:
        if isinstance(option, dict) and option.get("type") == "effort":
            values = option.get("values")
            if isinstance(values, list) and values:
                return [str(value) for value in values]
    return []


def _suffix_match(block, suffix):
    best = None
    for model_id in sorted((block or {})):
        if model_id.endswith("/" + suffix):
            values = _efforts(block, model_id)
            if values and (best is None or len(values) > len(best)):
                best = values
    return best or []


def resolve(api, provider_id, model_id):
    """Return (ladder, source) for one published model id."""
    own = api.get(provider_id) or {}
    values = _efforts(own.get("models"), model_id)
    if values:
        return values, provider_id

    tail = model_id.split("/")[-1]
    base = tail
    notag = base.split(":")[0]
    openrouter = api.get("openrouter") or {}
    for suffix in (base, notag):
        values = _suffix_match(openrouter.get("models"), suffix)
        if values:
            return values, "openrouter/" + suffix
    for provider in ("openrouter", "opencode-go"):
        block = (api.get(provider) or {}).get("models") or {}
        for key in (model_id, base, notag):
            values = _efforts(block, key)
            if values:
                return values, "%s/%s" % (provider, key)
    return [], None


def default_effort(values):
    """Highest safe default: xhigh when offered, else the top of the ladder."""
    if not values:
        return None
    if "xhigh" in values:
        return "xhigh"
    return values[-1]
