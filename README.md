# oar-pygeoadmin

OGC API Records service for SwissGeo, built on [pygeoapi](https://pygeoapi.io/) with an OpenSearch backend and multilingual record support.

| Branch | Status |
|--------|-----------|
| develop | ![Build Status](CODEBUILD_BADGE_URL) |
| main | ![Build Status](CODEBUILD_BADGE_URL) |

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

## Configuration

Provider registration in `pygeoapi-config.yml`:

```yaml
providers:
  - type: record
    name: swissgeo_provider.SwissGeoProvider
    data: ${OS_URL:-http://localhost:9200}/swissgeo-catalog
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
| `OS_URL` | `http://localhost:9200` | OpenSearch base URL |
| `PYGEOAPI_CONFIG` | `/pygeoapi/pygeoapi-config.yml` | pygeoapi config file path |
| `PYGEOAPI_OPENAPI` | `/pygeoapi/pygeoapi-openapi.yml` | OpenAPI spec file path |

## Running locally

```bash
docker compose up
```

This starts:
- **pygeoapi** on `http://localhost:5000` (uvicorn, via `app.py`)
- **OpenSearch** on port 9200
- **catalogue-loader** — one-shot container that loads records from `v0/` into OpenSearch
- **OpenSearch Dashboards** on `http://localhost:5602`

Copy `.env-docker` (or create one from `.env-local`) to configure environment variables before starting.

## Project structure

```
pygeoapi-swissgeo-extensions/
  app.py                  # Starlette entrypoint; patches call_api_threadsafe
  swissgeo_provider.py    # SwissGeoProvider: language selection + link patching
pygeoapi-config.yml       # pygeoapi server + collection configuration
pygeoapi-openapi.yml      # Generated OpenAPI specification
scripts/                  # Data loading scripts
v0/                       # Seed catalogue data
Dockerfile
docker-compose.yml
```
