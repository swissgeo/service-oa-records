FROM python:3.14-slim AS builder

WORKDIR /pygeoapi

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.14-slim AS production

WORKDIR /pygeoapi

ENV PYGEOAPI_CONFIG=/pygeoapi/pygeoapi-config.yml
ENV PYGEOAPI_OPENAPI=/pygeoapi/pygeoapi-openapi.yml
ENV PYTHONPATH=/pygeoapi/pygeoapi-swissgeo-extensions
ENV PATH="/pygeoapi/.venv/bin:$PATH"

RUN groupadd --gid 1001 pygeoapi \
 && useradd --uid 1001 --gid pygeoapi --no-create-home pygeoapi \
 && chown -R pygeoapi:pygeoapi /pygeoapi

COPY --from=builder --chown=pygeoapi:pygeoapi /pygeoapi/.venv /pygeoapi/.venv
COPY --chown=pygeoapi:pygeoapi pygeoapi-swissgeo-extensions /pygeoapi/pygeoapi-swissgeo-extensions
COPY --chown=pygeoapi:pygeoapi pygeoapi-config.yml /pygeoapi/pygeoapi-config.yml
COPY --chown=pygeoapi:pygeoapi pygeoapi-openapi.yml /pygeoapi/pygeoapi-openapi.yml
COPY --chown=pygeoapi:pygeoapi scripts /pygeoapi/scripts
COPY --chown=pygeoapi:pygeoapi static-s3 /pygeoapi/static-s3

USER pygeoapi

EXPOSE 8080

CMD ["uvicorn", "app:APP", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "/pygeoapi/pygeoapi-swissgeo-extensions"]
