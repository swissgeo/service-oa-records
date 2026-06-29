set shell := ["bash", "-c"]

run-docker-compose:
    docker compose up

# Index the swissgeo catalogue into the running OpenSearch
# possible steps:
#   generate: generate the catalogue data
#   index: create the index in OpenSearch
#   import: import the catalogue data into OpenSearch
etl-catalogue step="":
    FORCE=1 uv run python3 scripts/load-opensearch-catalogue.py {{step}}

cp-data:
    kubectl cp v0 oa-records/$(kubectl get pods -n oa-records -o jsonpath='{.items[0].metadata.name}'):/pygeoapi

fetch-api-from-s3:
    aws s3 --profile swisstopo-swissgeo-dev sync s3://oa-records-static-v2-dev-swissgeo static-s3

lint:
    uv run ruff check . --fix
