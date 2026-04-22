set shell := ["bash", "-c"]

update-openapi:
    set -a; source .env-local; set +a
    PYTHONPATH=`pwd`:`pwd`/pygeoapi-swissgeo-extensions PYGEOAPI_CONFIG=pygeoapi-config.yml PYGEOAPI_OPENAPI=pygeoapi-openapi.yml \
        uv run pygeoapi openapi generate pygeoapi-config.yml --output-file pygeoapi-openapi.yml

run-local: update-openapi
    PYTHONPATH=`pwd`:`pwd`/pygeoapi-swissgeo-extensions PYGEOAPI_CONFIG=pygeoapi-config.yml PYGEOAPI_OPENAPI=pygeoapi-openapi.yml \
        uv run pygeoapi serve

run-docker-compose:
    docker compose up

# Index the swissgeo catalogue into the running OpenSearch
load-catalogue:
    FORCE=1 uv run python3 scripts/load-opensearch-catalogue.py

cp-data:
    kubectl cp v0 oa-records/$(kubectl get pods -n oa-records -o jsonpath='{.items[0].metadata.name}'):/pygeoapi

fetch-api-from-s3:
    aws s3 --profile swisstopo-swissgeo-dev sync s3://oa-records-static-dev-swissgeo static-s3

lint:
    uv run ruff check . --fix
