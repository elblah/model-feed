# model-feed

Pre-processed LLM model metadata, served as small static JSON files.

Upstream sources are fetched, parsed, merged and normalised here (on a build
runner, not on the consumer box), then published as one small file per feed. A
consumer only downloads the file it needs (`opencode-go.json`,
`opencode-zen-free.json`, `nvidia.json`, ...) instead of the full 5.9 MB
`models.dev/api.json`, and gets all provider-specific rules (peak pricing, plan
caps, privacy tags, official rosters) already applied.

```
consumer  --GET out/<feed>.json-->  static host (GitHub Pages)
                                          ^
                                          |
                 build.py (cron / Actions) -- fetch, parse, merge, validate
```

## Design rules

- **Stdlib only.** No third-party packages, no lockfiles, no supply chain.
- **Never publish a broken run.** Every source must validate; on failure the
  previous good payload for that feed is kept and `manifest.json` records the
  error. Consumers see `stale = true` instead of garbage.
- **Deterministic output.** Sorted keys/order, no timestamps inside the data
  body. Identical input must produce byte-identical files, so conditional GET
  (`ETag` / `If-None-Match`) stays cheap and deploys are diff-noisy only when
  something actually changed.
- **Time-dependent values are pre-computed, not pre-decided.** Peak pricing is
  published as both `cost` (off-peak) and `cost_peak` plus the peak window
  metadata. The consumer picks by its own clock; no rebuild needed per hour.
- **Small files first.** Anything not needed to list or price a model stays out
  (descriptions, modalities detail, release notes).

## Feeds

A feed is not necessarily one provider: it can be a filtered view of one
(free models only) with columns joined from another page.

| feed file | upstream provider | extra columns |
| --- | --- | --- |
| `opencode-go.json` | `opencode-go` | peak pricing, plan caps, promos, `trains` |
| `opencode-zen-free.json` | `opencode` (cost 0 only) | privacy tags |
| `nvidia.json` | `nvidia` | official `/v1/models` roster |
| `cline-pass.json` | `cline-pass` | -- |

## Layout

```
model-feed/
  build.py              orchestrator: TARGETS -> fetch -> normalise -> out/
  lib/fetch.py          HTTP GET with gzip + conditional request + disk cache
  lib/keys.py           name/id join keys shared by sources
  lib/peaks.py          peak-window rules (DeepSeek 2x, Mon-Fri UTC)
  lib/schema.py         normalisation helpers + schema version
  sources/modelsdev.py  models.dev/api.json -> per-feed catalogs
  sources/gocaps.py     opencode.ai Go page -> plan caps, promos, trains
  sources/zenprivacy.py opencode.ai models page -> privacy tags
  sources/official.py   provider /v1/models rosters (nvidia, chutes)
  out/                  generated: <feed>.json + manifest.json
```

## Output schema (v1)

`out/<feed>.json`

```json
{
  "schema": 1,
  "feed": "opencode-go",
  "generated_at": "2026-11-03T15:35:37Z",
  "provider": {
    "id": "opencode-go",
    "name": "OpenCode Go",
    "api": "https://opencode.ai/zen/go/v1",
    "docs": "https://opencode.ai/v2/docs/console/go/",
    "env": ["OPENCODE_API_KEY"],
    "npm": "@ai-sdk/openai-compatible"
  },
  "source": {"url": "https://models.dev/api.json", "etag": "\"abc\"", "fetched_at": "..."},
  "levers": {
    "peak": {"enabled": true, "models": ["deepseek-v4-pro", "deepseek-v4.1-flash"]},
    "caps": {"enabled": true, "plan_default": "go",
             "plans": [{"id": "go", "name": "Go", "price_usd": 10.0, "covers": "..."}]},
    "privacy": false,
    "official": false
  },
  "models": [
    {
      "id": "deepseek-v4.1-flash",
      "name": "DeepSeek V4.1 Flash",
      "family": "deepseek",
      "context": 1048576,
      "max_output": 131072,
      "cost": {"in": 0.15, "out": 0.6, "cache_read": 0.003, "cache_write": 0},
      "cost_peak": {"in": 0.3, "out": 1.2, "cache_read": 0.006, "cache_write": 0},
      "peak": {"mult": 2, "windows": [{"days": "Mon-Fri", "start": "01:00", "end": "04:00", "tz": "UTC"}]},
      "free": false,
      "tool_call": true,
      "reasoning": true,
      "open_weights": true,
      "release_date": "2026-10-23",
      "api": "https://opencode.ai/zen/go/v1",
      "cap_usd": {"go": 100.0, "plus": 200.0, "teams": 105.0},
      "trains": true
    }
  ]
}
```

Optional model keys, emitted only when a source provides them:

| key | meaning |
| --- | --- |
| `upstream_id` | real endpoint id when it differs from `id` |
| `cost_peak` + `peak` | same price inside a peak window (`mult` x) |
| `cap_usd` | monthly $ usage included per plan id (`levers.caps.plans`) |
| `promo` | per-plan promo label from the caps table (`{"go": "new"}`) |
| `trains` | request data may be used for training |
| `privacy` | privacy tag: `trains`, `zdr`, `zdr-retained` |
| `status` | `deprecated` = gone from the official roster; clients hide it |
| `official` | listed by the official `/v1/models` endpoint |
| `bare` | id came from `/v1/models` only: no prices, generic limits |

`out/manifest.json`

```json
{
  "schema": 1,
  "generated_at": "...",
  "source": {"url": "https://models.dev/api.json", "etag": "\"abc\"", "from_cache": false},
  "providers": {
    "opencode-go": {"file": "opencode-go.json", "sha256": "...", "bytes": 15825,
                    "models": 37, "provider": "opencode-go",
                    "stale": false, "error": null}
  }
}
```

## Usage

```
python3 build.py                             # every feed, write out/
python3 build.py --feed opencode-go,nvidia
python3 build.py --out /tmp/mi --no-network  # use cached upstream copies
```

Exit codes: `0` all feeds written, `1` some feed failed (last good payload
kept), `2` fatal (upstream unreadable or unknown feed name).

## Publishing

`.github/workflows/feed.yml` rebuilds every 30 minutes and on demand, then
deploys `out/` as a Pages artifact. Nothing is committed back; the site is
replaced wholesale each run. The only credentials are the automatic
`GITHUB_TOKEN` scopes (`pages: write`, `id-token: write`) - no secrets to
create or rotate.

One-time setup: push this directory to a public repo, then Settings -> Pages ->
Source: GitHub Actions (the workflow also sets it via `enablement: true`).

Notes:

- Free Pages requires a public repo. Public repos get unlimited Actions
  minutes; a run costs about 7 s.
- Scheduled workflows are disabled automatically after 60 days without repo
  activity; re-enable from the Actions tab if it happens.
- The first run downloads the full upstream set (~6 MB) and the `.cache` key
  changes every run, so continuations usually get `304 Not Modified`. Delete
  the `upstream-*` caches if a bad body ever gets sticky.
- A failed build exits non-zero, so the job fails and the previous deployment
  stays live. There is no half-published state.

## Status

- [x] schema v1 + manifest
- [x] models.dev source (normalised, deterministic)
- [x] DeepSeek peak pricing (windows + `cost_peak`)
- [x] opencode-go plan caps (go / plus / teams) + promos + training flags
- [x] opencode zen privacy tags
- [x] nvidia nim official-roster merge
- [x] clinepass prices (full cline-pass roster)
- [x] GitHub Actions workflow (30 min cron + dispatch) + Pages publish
- [x] first deployed run verified against the live URL
- [ ] thin client (manifest -> conditional GET -> list/predict)
