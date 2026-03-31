set shell := ["bash", "-c"]

run-local:
    set -a; source .env-local; set +a
    PYTHONPATH=`pwd`:`pwd`/pygeoapi-swissgeo-extensions PYGEOAPI_CONFIG=pygeoapi-config.yml PYGEOAPI_OPENAPI=pygeoapi-openapi.yml \
        uv run pygeoapi openapi generate pygeoapi-config.yml --output-file pygeoapi-openapi.yml
    PYTHONPATH=`pwd`:`pwd`/pygeoapi-swissgeo-extensions PYGEOAPI_CONFIG=pygeoapi-config.yml PYGEOAPI_OPENAPI=pygeoapi-openapi.yml \
        uv run pygeoapi serve

run-docker-compose:
    docker compose up

# Index the swissgeo catalogue into the running OpenSearch
load-catalogue:
    FORCE=1 uv run python3 scripts/load-opensearch-catalogue.py

# Index the swissgeo catalogue into the running OpenSearch
load-sample-records:
    FORCE=1 uv run python3 scripts/load-sample-records.py
