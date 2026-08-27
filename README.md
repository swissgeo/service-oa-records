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
OpenSearch
```

## swissgeo_provider.py

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

> [!IMPORTANT]
> This service does not start any backing services of its own. It expects the
> [`service-control`](https://github.com/swissgeo/service-control) stack to be running,
> which provides OpenSearch on `localhost:9200` (with the catalogue indexes loaded) and
> the OTLP collector on `localhost:4317`. Start that stack first.
> Also run `manage.py oar_opensearch_export` at least once to create the OpenSearch indexes and write the data.

### Setup

```bash
make setup
```

Creates the virtualenv with `uv sync`, creates `.env` from `.env.default` if it is missing,
and drops you into a shell with those variables exported. `.env` is gitignored, so local
overrides (e.g. a different `OPENSEARCH_URL`) survive. A `.env.local`, if present, takes
precedence over `.env`.

### Run on the host

```bash
make serve
```



### Run the built image

```bash
make dockerrun
```

Builds the image and runs it with `--net=host`, so it reaches the same
`localhost:9200` / `localhost:4317` as `make serve`. This is for verifying the deployment
artifact; day-to-day development uses `make serve`.

## Debugging

```bash
make serve-debug
```

Runs the app under [debugpy](https://github.com/microsoft/debugpy) listening on port 5678 and
waiting for a client, so you can attach before the first request is handled.

Then attach your debugger (e.g. **"Attach to Docker (swissgeo_provider)"** in Zed) to
`localhost:5678`.

## Project structure

```
pygeoapi-swissgeo-extensions/
  app.py                  # Starlette entrypoint; patches call_api_threadsafe
  swissgeo_provider.py    # SwissGeoProvider: language selection + link patching
pygeoapi-config.yml       # pygeoapi server + collection configuration
static-s3/                # 1:1 catalog data from S3
```
