"""Synthetic check endpoint reporting whether the service and OpenSearch are healthy.

The check verifies that every OpenSearch index (or alias) used by a
``SwissGeoProvider`` in the pygeoapi configuration exists and is reachable.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from opentelemetry import trace
from starlette.requests import Request
from starlette.responses import JSONResponse

from schemas.syntheticz import (
    Syntheticz,
    SyntheticzExternalSystem,
    SyntheticzServiceDetails,
    SyntheticzStatus,
)
from settings import get_settings
from swissgeo_provider import SwissGeoProvider
from version import __version__

LOGGER = logging.getLogger(__name__)

_tracer = trace.get_tracer(__name__)

_OPENSEARCH = "opensearch"

_PROVIDER_NAME = "swissgeo_provider.SwissGeoProvider"


def _opensearch_providers(resources: dict) -> list[dict]:
    """Return the SwissGeoProvider definitions of the pygeoapi resources."""
    providers = [
        provider
        for resource in resources.values()
        for provider in resource.get("providers", [])
        if provider.get("name") == _PROVIDER_NAME
    ]
    if len(providers) == 0:
        raise ValueError("No OpenSearch providers found")
    return providers


def _check_opensearch(providers: list[dict]) -> None:
    """Raise if one of the providers' indexes is missing or OpenSearch is unreachable."""
    for provider_def in providers:
        # Like pygeoapi on each request: the first instance per index connects, later
        # ones reuse the shared OpenSearch client. A failure to connect raises.
        provider = SwissGeoProvider(provider_def)
        if not provider.os_.indices.exists(index=provider.index_name):
            raise LookupError(f"OpenSearch index {provider.index_name} does not exist")


async def check(resources: dict) -> Syntheticz:
    settings = get_settings()
    syntheticz = Syntheticz(
        service=SyntheticzServiceDetails(name=settings.SERVICE_NAME, version=__version__),
        status=SyntheticzStatus.UP,
        external_systems={_OPENSEARCH: SyntheticzExternalSystem(status=SyntheticzStatus.UP)},
    )

    with _tracer.start_as_current_span("syntheticz-opensearch") as span:
        span.set_attribute("syntheticz", True)  # noqa: FBT003 ignore boolean-positional-value-in-call here
        try:
            # The OpenSearch client is blocking, so it runs in the default executor.
            await asyncio.to_thread(_check_opensearch, _opensearch_providers(resources))
        except Exception:
            # We explicitly use a broad exception here, as we want to be sure that any
            # exception is returning DOWN status, even if we don't know the exact reason.
            LOGGER.exception("Failed to access OpenSearch")
            syntheticz.status = SyntheticzStatus.DOWN
            syntheticz.external_systems[_OPENSEARCH].status = SyntheticzStatus.DOWN

    return syntheticz


def syntheticz_endpoint(resources: dict) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Return the synthetic check endpoint for the given pygeoapi resources."""

    async def get_syntheticz(_request: Request) -> JSONResponse:
        health = await check(resources)
        return JSONResponse(
            health.model_dump(mode="json"),
            status_code=200 if health.status == SyntheticzStatus.UP else 500,
            headers={"Cache-Control": "no-store"},
        )

    return get_syntheticz
