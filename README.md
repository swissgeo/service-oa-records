# service-oa-records

OGC API Records service for SwissGeo, built on [pygeoapi](https://pygeoapi.io/) with an OpenSearch backend and multilingual record support.

| Branch | Status | Coverage |
|--------|-----------|-------|
| develop | ![Build Status](https://codebuild.eu-central-1.amazonaws.com/badges?uuid=eyJlbmNyeXB0ZWREYXRhIjoiaDdjQ3ROL0NVSzQyRFN5Ukpjc282ZjE1cjc4N25RcHNWVVliRE9laEgzbUdmcmxFdHEyTWxDNkc1ZU9rVU95T0ZRazZ0RS9mRFphMXpVeVZ1VDBmSXZFPSIsIml2UGFyYW1ldGVyU3BlYyI6Ii9GZlhsODB4QkJ4UHgrVDIiLCJtYXRlcmlhbFNldFNlcmlhbCI6MX0%3D&branch=develop) | [![codecov-develop](https://codecov.io/gh/swissgeo/service-oa-records/branch/develop/graph/badge.svg)](https://codecov.io/gh/swissgeo/service-oa-records) |
| main | ![Build Status](https://codebuild.eu-central-1.amazonaws.com/badges?uuid=eyJlbmNyeXB0ZWREYXRhIjoiaDdjQ3ROL0NVSzQyRFN5Ukpjc282ZjE1cjc4N25RcHNWVVliRE9laEgzbUdmcmxFdHEyTWxDNkc1ZU9rVU95T0ZRazZ0RS9mRFphMXpVeVZ1VDBmSXZFPSIsIml2UGFyYW1ldGVyU3BlYyI6Ii9GZlhsODB4QkJ4UHgrVDIiLCJtYXRlcmlhbFNldFNlcmlhbCI6MX0%3D&branch=main) | [![codecov-main](https://codecov.io/gh/swissgeo/service-oa-records/branch/main/graph/badge.svg)](https://codecov.io/gh/swissgeo/service-oa-records) |

> [!NOTE]
> This is still in POC phase

## Overview

This service exposes Swiss geospatial catalogue data as an OGC API Records endpoint. pygeoapi handles the OGC API layer; records are stored in OpenSearch and queried via `SwissGeoProvider`, a custom provider that adds language-aware field selection and link patching on top of pygeoapi's built-in `OpenSearchCatalogueProvider`.

```
Client
  │  ?lang=de&f=json
  ▼
uvicorn (app.py)          ← patches call_api_threadsafe to inject lang/fmt
  │                          into the executor thread-local before each call
  ▼
pygeoapi Starlette app
  │
  ▼
SwissGeoProvider          ← extends OpenSearchCatalogueProvider
  │  query() / get()
  ├─ reads lang from thread-local (set by app.py)
  ├─ calls super().query() / super().get()
  ├─ _apply_lang()  – overwrites title/description with localised variants
  └─ _patch_links() – appends ?lang=…&f=… to same-host links
  │
  ▼
OpenSearch  (swissgeo-catalog / swissgeo-distributions index)
```

## swissgeo_provider.py



## Language handling

OpenSearch records carry per-language fields alongside the generic ones:

| Generic field | Localised variants |
|---|---|
| `title` | `title_de`, `title_fr`, `title_it`, `title_en` |
| `description` | `description_de`, `description_fr`, `description_it`, `description_en` |

`SwissGeoProvider._apply_lang()` promotes the requested language's variant into the generic `title`/`description` fields and strips all per-language fields before pygeoapi serialises the response. Supported languages: `en`, `de`, `fr`, `it` (falls back to `en`).

### Why `app.py` is needed

pygeoapi's Starlette integration runs provider calls in a thread pool. By the time the provider executes, the Starlette request context is no longer accessible. `app.py` monkey-patches `call_api_threadsafe` to call `set_request_params(lang, fmt)` just before dispatching each call, storing the values in a `threading.local` that `SwissGeoProvider` reads. Flask users are handled via a fallback `flask.request.args` read inside `_get_lang_and_fmt()`.

### Link patching

`_patch_links()` appends `?lang=<lang>&f=<fmt>` to any link whose `href` is relative or starts with `PYGEOAPI_SERVER_URL`. External links are left untouched.

## Sorting

Requests that don't pass `sortby` get a default ordering, so results are reproducible and offset paging never skips or repeats a record:

| Request | Default order |
|---|---|
| Browsing (no `q`) | `title.<lang>` ascending, then `id` |
| Free-text search (`q=…`) | `_score` descending, then `id` |

`id` is always the last key: OpenSearch gives no ordering guarantee between documents that tie on the primary key, which makes `offset`/`limit` paging unstable. Search keeps `_score` first because *any* explicit sort clause makes OpenSearch drop relevance scoring — sorting search hits alphabetically would bury the best match.

The sort language is the one pygeoapi negotiated for the request, so the order always matches the titles actually rendered. A record with no title in that language sorts last.

Clients can override with e.g. `?sortby=title.fr`, `?sortby=-title.de` or `?sortby=id`.

### Index requirements

OpenSearch **cannot sort on a `text` field** — it rejects the query with `Text fields are not optimised for operations that require per-document field data`. Each `properties.title.<lang>` therefore carries a `raw` keyword sub-field:

```json
"de": {"type": "text", "fields": {"raw": {"type": "keyword", "normalizer": "sortable", "ignore_above": 1024}}}
```

Two details are load-bearing:

- **The sub-field must be named `raw`.** pygeoapi hardcodes a `.raw` suffix when sorting a field it considers a string (`pygeoapi/provider/opensearch_.py`), so `keyword` — the more common convention — would not be found.
- **The `sortable` normalizer** (`lowercase` + `asciifolding`, defined under `settings.analysis`) gives real alphabetical order. Raw keyword sorting is byte order, which would place `Öschinensee` after `Zürich` and every lowercase title after every uppercase one.

`asciifolding` is a built-in filter, so no `analysis-icu` plugin is needed on the managed AWS domain.

> [!IMPORTANT]
> These are mapping changes: existing indexes must be recreated and reloaded, otherwise the `raw` sub-field is missing and every record sorts as if it had no title.
> ```bash
> just etl-catalogue          # FORCE=1, recreates the indexes and reimports
> ```

Because `title.<lang>`, `id` and `_score` are registered as provider fields, they also show up as queryables.

## Configuration

Provider registration in `pygeoapi-config.yml`:

```yaml
providers:
  - type: record
    name: swissgeo_provider.SwissGeoProvider
    data: ${OPENSEARCH_URL:-http://localhost:9200}/swissgeo-catalog
    id_field: externalId
    time_field: recordCreated
    title_field: title
    languages:
      - en
      - de
      - fr
      - it
```

Key environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PYGEOAPI_SERVER_URL` | `/` | Base URL used to identify same-host links for patching |
| `OPENSEARCH_URL` | `http://localhost:9200` | OpenSearch base URL |
| `PYGEOAPI_CONFIG` | `/pygeoapi/pygeoapi-config.yml` | pygeoapi config file path |

## Running locally

```bash
docker compose up
```

This starts:
- **pygeoapi** on `http://localhost:8080/api/oar/r/` (uvicorn, via `app.py`)
- **OpenSearch** on port 9200
- **catalogue-loader** — one-shot container that loads records from `static-s3/` into OpenSearch
- **OpenSearch Dashboards** on `http://localhost:5602`

Copy `.env-docker` (or create one from `.env-local`) to configure environment variables before starting.

## Debugging

Have ENV `PYDEBUG=true` set.

```bash
PYDEBUG=true docker compose --profile debug up
```

This runs pygeoapi under [debugpy](https://github.com/microsoft/debugpy) listening on port 5678, with the local `pygeoapi-swissgeo-extensions/` directory mounted into the container so edits are reflected without a rebuild.

Then attach your debugger (e.g. **"Attach to Docker (swissgeo_provider)"** in Zed) to `localhost:5678`.

## Project structure

```
pygeoapi-swissgeo-extensions/
  app.py                  # Starlette entrypoint; patches call_api_threadsafe
  swissgeo_provider.py    # SwissGeoProvider: language selection + link patching
pygeoapi-config.yml       # pygeoapi server + collection configuration
scripts/                  # Data loading scripts
static-s3/                # 1:1 catalog data from S3
```
