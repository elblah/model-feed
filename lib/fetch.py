"""HTTP helpers: gzip-aware GET, conditional requests, on-disk cache.

Stdlib only. Every network call in models-info goes through here so that
caching/offline behaviour stays uniform across sources.
"""

import gzip
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
import zlib

UA = "models-info/1 (static model metadata feed)"
TIMEOUT = 60
CACHE_DIR_DEFAULT = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "models-info",
)


class FetchError(Exception):
    pass


def get(url, etag=None, timeout=TIMEOUT):
    """GET a URL.

    Returns (status, body_bytes, etag). status is 304 when the caller's etag
    still matches (body is then None).
    """
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower().strip()
            if enc == "gzip":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw)
            return 200, raw, resp.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, etag
        raise FetchError("HTTP %s for %s" % (exc.code, url)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError("%s for %s" % (exc, url)) from exc


def _atomic_write(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _utc_iso(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def fetch_cached(url, name, cache_dir=None, no_network=False, max_age=None):
    """Fetch url with a body+meta cache pair.

    Returns (body, meta) where meta carries url/etag/fetched_at/from_cache/
    status/error. A failure never clobbers the cache: the stale body is
    returned with meta["error"] set. Raises only when nothing is cached.

    max_age (seconds) skips the request entirely while the cached copy is
    younger than that — for pages that serve no useful validators.
    """
    cache_dir = cache_dir or CACHE_DIR_DEFAULT
    body_path = os.path.join(cache_dir, name)
    meta_path = body_path + ".meta.json"

    meta = {"url": url, "etag": None, "fetched_at": None, "from_cache": True,
            "status": 0, "error": None}
    cached_body = None
    body_age = None
    if os.path.exists(body_path):
        with open(body_path, "rb") as handle:
            cached_body = handle.read()
        body_age = max(0.0, time.time() - os.path.getmtime(body_path))
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    old = json.load(handle)
                meta["etag"] = old.get("etag")
                meta["fetched_at"] = old.get("fetched_at")
            except (OSError, ValueError):
                pass

    if max_age is not None and cached_body is not None and body_age < max_age:
        meta["status"] = 304
        return cached_body, meta

    if no_network:
        if cached_body is None:
            raise FetchError("no network allowed and no cached copy of %s" % url)
        meta["error"] = "no-network"
        return cached_body, meta

    try:
        status, body, etag = get(url, etag=meta["etag"])
    except FetchError as exc:
        if cached_body is None:
            raise
        meta["error"] = str(exc)
        return cached_body, meta

    if status == 304 and cached_body is not None:
        meta["fetched_at"] = _utc_iso()
        meta["from_cache"] = True
        meta["status"] = 304
        _atomic_write(meta_path, json.dumps(meta, sort_keys=True).encode())
        return cached_body, meta

    _atomic_write(body_path, body)
    meta.update({"etag": etag, "fetched_at": _utc_iso(), "from_cache": False,
                 "status": status, "error": None})
    _atomic_write(meta_path, json.dumps(meta, sort_keys=True).encode())
    return body, meta


def fetch_json(url, name, cache_dir=None, no_network=False, max_age=None):
    body, meta = fetch_cached(url, name, cache_dir, no_network, max_age=max_age)
    try:
        return json.loads(body.decode("utf-8")), meta
    except ValueError as exc:
        raise FetchError("invalid JSON from %s: %s" % (url, exc)) from exc
