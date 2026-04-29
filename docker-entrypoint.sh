#!/usr/bin/env bash

pygeoapi openapi generate pygeoapi-config.yml --output-file pygeoapi-openapi.yml

if [ "${PYDEBUG}" = "true" ]; then
    echo PYDEBUG mode enabled!
    python -m debugpy --listen 0.0.0.0:5678 -m uvicorn app:APP --host 0.0.0.0 --port 8080 --app-dir /pygeoapi/pygeoapi-swissgeo-extensions --log-config /pygeoapi/config-files/logging-conf-otel.yaml
else
    uvicorn app:APP --host 0.0.0.0 --port 8080 --app-dir /pygeoapi/pygeoapi-swissgeo-extensions --log-config /pygeoapi/config-files/logging-conf-otel.yaml
fi
