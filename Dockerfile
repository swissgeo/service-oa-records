FROM ghcr.io/osgeo/gdal:ubuntu-small-latest

WORKDIR /pygeoapi

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv --break-system-packages

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY pygeoapi-swissgeo-extensions /pygeoapi/pygeoapi-swissgeo-extensions
COPY scripts /pygeoapi/scripts

ENV PYGEOAPI_CONFIG=/pygeoapi/pygeoapi-config.yml
ENV PYGEOAPI_OPENAPI=/pygeoapi/pygeoapi-openapi.yml
ENV PYTHONPATH=/pygeoapi/pygeoapi-swissgeo-extensions
ENV PATH="/pygeoapi/.venv/bin:$PATH"

EXPOSE 8080

CMD ["uvicorn", "app:APP", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "/pygeoapi/pygeoapi-swissgeo-extensions"]
