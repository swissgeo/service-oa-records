FROM python:3.14-slim AS builder

WORKDIR /pygeoapi

RUN pip install uv

# Set to true to include debugpy, for the PYDEBUG mode of docker-entrypoint.sh
ARG INSTALL_DEBUGPY=false

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev $([ "$INSTALL_DEBUGPY" = "true" ] && echo "--group debug")

FROM python:3.14-slim AS production

WORKDIR /pygeoapi

ENV PYGEOAPI_CONFIG=/pygeoapi/pygeoapi-config.yml
ENV PYGEOAPI_OPENAPI=/pygeoapi/pygeoapi-openapi.yml
ENV PYTHONPATH=/pygeoapi/pygeoapi-swissgeo-extensions
ENV PATH="/pygeoapi/.venv/bin:$PATH"
# Fewer glibc malloc arenas keep the memory freed by the worker threads from
# fragmenting across arenas and inflating the RSS.
ENV MALLOC_ARENA_MAX=2
# uvicorn answers 503 once this many connections or requests are open, instead
# of accepting more work than fits in memory.
ENV UVICORN_LIMIT_CONCURRENCY=100

RUN groupadd --gid 1001 pygeoapi \
 && useradd --uid 1001 --gid pygeoapi --no-create-home pygeoapi \
 && chown -R pygeoapi:pygeoapi /pygeoapi

COPY --from=builder --chown=pygeoapi:pygeoapi /pygeoapi/.venv /pygeoapi/.venv
COPY --chown=pygeoapi:pygeoapi pygeoapi-swissgeo-extensions /pygeoapi/pygeoapi-swissgeo-extensions
COPY --chown=pygeoapi:pygeoapi pygeoapi-config.yml /pygeoapi/pygeoapi-config.yml
COPY --chown=pygeoapi:pygeoapi docker-entrypoint.sh /pygeoapi/docker-entrypoint.sh
COPY --chown=pygeoapi:pygeoapi config-files /pygeoapi/config-files

USER pygeoapi

EXPOSE 80

ENTRYPOINT [ "./docker-entrypoint.sh" ]
