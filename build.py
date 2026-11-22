#!/usr/bin/env python3
"""Build the static model-metadata feed.

Fetches upstream sources once, normalises them, and writes one small JSON file
per feed plus a manifest. stdout stays empty (data goes to files); progress and
warnings go to stderr, so the script is safe to run from cron/Actions.

    python3 build.py                             # every feed
    python3 build.py --feed opencode-go,nvidia
    python3 build.py --out /tmp/mi --no-network  # offline, from cache

A feed is not always a whole provider: some are a filtered view of one, with
extra columns joined in from another page (plan caps, privacy tags, an official
/v1/models list).
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import schema  # noqa: E402
from lib.fetch import FetchError  # noqa: E402
from sources import (clinepass, clinerec, effort, gocaps, modelsdev,
                     official, zenprivacy)  # noqa: E402

MANIFEST = "manifest.json"

# feed id -> upstream provider id + the columns joined in from other sources.
TARGETS = (
    {"feed": "opencode-go", "source": "opencode-go", "caps": True,
     "official": "opencode-go", "drop_deprecated": True},
    {"feed": "opencode-zen-free", "source": "opencode",
     "free_only": True, "privacy": True},
    {"feed": "nvidia", "source": "nvidia", "official": "nvidia"},
    {"feed": "cline-pass", "source": "cline-pass", "prices": "clinepass"},
)


def log(message):
    sys.stderr.write(message + "\n")


def atomic_write(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        # mkstemp creates 0600; published files must be world-readable.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def existing_manifest(out_dir):
    path = os.path.join(out_dir, MANIFEST)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("providers") or {}
    except (OSError, ValueError):
        return {}


class Enrichment:
    """Lazily loaded side sources; each is fetched at most once per run."""

    def __init__(self, cache_dir, no_network):
        self.cache_dir = cache_dir
        self.no_network = no_network
        self._caps = None
        self._privacy = None
        self._prices = {}
        self._roster = None
        self._official = {}

    def caps(self):
        if self._caps is None:
            try:
                payload, _meta = gocaps.load(self.cache_dir, self.no_network)
            except FetchError as exc:
                log("  warn  go caps unavailable: %s" % exc)
                payload = {"models": {}}
            log("  caps  %d models, plans: %s"
                % (len(payload.get("models") or {}),
                   ", ".join(plan["id"] for plan in payload.get("plans") or [])))
            self._caps = payload
        return self._caps

    def privacy(self):
        if self._privacy is None:
            payload, _meta = zenprivacy.load(self.cache_dir, self.no_network)
            log("  priv  %d tagged entries" % len(payload.get("tags") or {}))
            self._privacy = payload
        return self._privacy

    def official(self, provider):
        if provider not in self._official:
            ids, _meta = official.load(provider, self.cache_dir, self.no_network)
            log("  offi  %s: %d ids" % (provider, len(ids or [])))
            self._official[provider] = ids or []
        return self._official[provider]

    def prices(self, name):
        """Reference-price tables that beat models.dev (docs are the truth)."""
        if name not in self._prices:
            loaders = {"clinepass": clinepass.load}
            if name not in loaders:
                raise FetchError("unknown price source %r" % name)
            prices, _meta = loaders[name](self.cache_dir, self.no_network)
            log("  price %s: %d rows" % (name, len(prices)))
            self._prices[name] = prices
        return self._prices[name]

    def roster(self):
        """Cline offered-model roster; optional, so failures are non-fatal."""
        if self._roster is None:
            try:
                self._roster, meta = clinerec.load(self.cache_dir, self.no_network)
                log("  rost  %d offered, %d free%s"
                    % (len(self._roster["roster"]), len(self._roster["free"]),
                       "" if not meta.get("error") else " (stale: %s)" % meta["error"]))
            except FetchError as exc:
                log("  rost  unavailable: %s" % exc)
                self._roster = {"roster": [], "free": []}
        return self._roster


def enrich_cline_pass(payload, api, provider_id, prices, helpers):
    """Docs prices + allowance + roster + resolved effort ladders."""
    entries = payload["models"]
    priced, unpriced = clinepass.apply(entries, prices)
    extra = clinepass.docs_entries(prices, [item["id"] for item in entries])
    if extra:
        entries.extend(extra)
        entries.sort(key=lambda item: item["id"])
        log("  note  %d docs-only model(s) added: %s"
            % (len(extra), ", ".join(item["id"] for item in extra)))

    resolved = 0
    for entry in entries:
        ladder, source = effort.resolve(api, provider_id, entry["id"])
        if ladder:
            entry["effort_ladder"] = ladder
            entry["effort_default"] = effort.default_effort(ladder)
            resolved += 1
        entry["effort_source"] = source or ""

    payload["levers"]["prices"] = {
        "source": clinepass.URL,
        "priced": len(priced),
        "modelsdev_fallback": unpriced,
    }
    payload["levers"]["caps"] = dict(clinepass.allowance(), enabled=True)
    payload["levers"]["effort"] = {"policy": effort.DEFAULT_POLICY,
                                   "resolved": resolved}
    roster = helpers.roster()
    payload["roster"] = {"provider": provider_id,
                         "ids": roster["roster"],
                         "free": roster["free"]}


def build_one(api, target, source_meta, helpers, out_dir, pretty):
    """Write one feed file. Returns (feed id, manifest entry, payload)."""
    feed_id = target["feed"]
    kwargs = {"feed_id": feed_id, "free_only": target.get("free_only", False),
              "drop_deprecated": target.get("drop_deprecated", False)}
    if target.get("caps"):
        kwargs["caps"] = helpers.caps()
    if target.get("privacy"):
        kwargs["privacy"] = helpers.privacy()
    if target.get("official"):
        roster = helpers.official(target["official"])
        if not roster:
            # An empty roster would flag every model as deprecated; refuse.
            raise FetchError("official roster for %r unavailable"
                             % target["official"])
        kwargs["official"] = roster

    if target.get("prices"):
        kwargs["prices"] = helpers.prices(target["prices"])

    payload = modelsdev.build_provider(api, target["source"], source_meta,
                                       **kwargs)
    dropped = (payload["levers"].get("deprecated") or {}).get("dropped")
    if dropped:
        log("  drop  %s: %d deprecated model(s)" % (feed_id, dropped))
    if target.get("prices") == "clinepass":
        enrich_cline_pass(payload, api, target["source"], kwargs["prices"],
                          helpers)
    data = modelsdev.dumps(payload, pretty=pretty)
    name = "%s.json" % feed_id
    atomic_write(os.path.join(out_dir, name), data)
    entry = {
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "models": len(payload["models"]),
        "provider": payload["provider"]["id"],
        "etag": payload["source"].get("etag"),
        "generated_at": payload.get("generated_at"),
        "stale": False,
        "error": None,
    }
    return feed_id, entry


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the model-feed feed")
    parser.add_argument("--feed", "--providers", dest="feed", default="all",
                        help="comma-separated feed ids, or 'all' (default)")
    parser.add_argument("--out", default=os.path.join(HERE, "out"),
                        help="output directory (default: <repo>/out)")
    parser.add_argument("--cache-dir", default=None,
                        help="upstream cache directory")
    parser.add_argument("--no-network", action="store_true",
                        help="never fetch, use the cached upstream copy")
    parser.add_argument("--pretty", action="store_true",
                        help="indent the JSON (debugging; bigger files)")
    args = parser.parse_args(argv)

    log("model-feed: schema v%d" % schema.SCHEMA)
    try:
        api, source_meta = modelsdev.fetch_api(args.cache_dir, args.no_network)
    except FetchError as exc:
        log("FATAL: cannot load models.dev: %s" % exc)
        return 2
    log("models.dev: %d providers, etag=%s, fetched_at=%s%s"
        % (len(api), source_meta.get("etag"), source_meta.get("fetched_at"),
           " (from cache)" if source_meta.get("from_cache") else ""))

    wanted = args.feed.strip()
    if wanted == "all":
        targets = list(TARGETS)
    else:
        names = [item.strip() for item in wanted.split(",") if item.strip()]
        by_feed = {target["feed"]: target for target in TARGETS}
        unknown = [name for name in names if name not in by_feed]
        if unknown:
            log("FATAL: unknown feed(s): %s (known: %s)"
                % (", ".join(unknown), ", ".join(by_feed)))
            return 2
        targets = [by_feed[name] for name in names]

    missing = sorted({target["source"] for target in targets} - set(api))
    if missing:
        log("FATAL: upstream provider(s) gone from models.dev: %s"
            % ", ".join(missing))
        return 2

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    previous = existing_manifest(out_dir)
    helpers = Enrichment(args.cache_dir, args.no_network)

    manifest = {"schema": schema.SCHEMA,
                "generated_at": source_meta.get("fetched_at"),
                "source": {"url": modelsdev.URL, "etag": source_meta.get("etag"),
                           "from_cache": bool(source_meta.get("from_cache"))},
                "providers": {}}

    failures = 0
    for target in targets:
        feed_id = target["feed"]
        try:
            feed_id, entry = build_one(api, target, source_meta, helpers,
                                       out_dir, args.pretty)
            manifest["providers"][feed_id] = entry
            log("  ok   %-20s %5d models %7d bytes  (%s)"
                % (feed_id, entry["models"], entry["bytes"], entry["provider"]))
        except (FetchError, KeyError, TypeError, ValueError) as exc:
            failures += 1
            # Keep the last good payload: never publish a hole.
            keep = previous.get(feed_id)
            if keep:
                merged = dict(keep)
                merged.update({"stale": True, "error": str(exc)})
                manifest["providers"][feed_id] = merged
            else:
                manifest["providers"][feed_id] = {
                    "file": None, "bytes": 0, "sha256": None, "models": 0,
                    "stale": True, "error": str(exc)}
            log("  FAIL %-20s %s" % (feed_id, exc))

    data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(os.path.join(out_dir, MANIFEST), data)

    published = sum(1 for item in manifest["providers"].values()
                    if item.get("file") and not item.get("stale"))
    log("wrote %d feed file(s) (%d failed) + %s to %s"
        % (published, failures, MANIFEST, out_dir))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
