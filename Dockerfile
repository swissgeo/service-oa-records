FROM python:3.14-slim

WORKDIR /pygeoapi

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY pygeoapi-swissgeo-extensions /pygeoapi/pygeoapi-swissgeo-extensions
COPY pygeoapi-config.yml /pygeoapi/pygeoapi-config.yml
COPY pygeoapi-openapi.yml /pygeoapi/pygeoapi-openapi.yml
COPY scripts /pygeoapi/scripts

ENV PYGEOAPI_CONFIG=/pygeoapi/pygeoapi-config.yml
ENV PYGEOAPI_OPENAPI=/pygeoapi/pygeoapi-openapi.yml
ENV PYTHONPATH=/pygeoapi/pygeoapi-swissgeo-extensions
ENV PATH="/pygeoapi/.venv/bin:$PATH"

EXPOSE 8080

CMD ["uvicorn", "app:APP", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "/pygeoapi/pygeoapi-swissgeo-extensions"]
