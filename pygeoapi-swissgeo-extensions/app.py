"""
Starlette app entrypoint for uvicorn.

Patches call_api_threadsafe to inject the ``lang`` query param into the
executor thread via a thread-local, working around a pygeoapi bug where
get_plugin_locale receives a plain string instead of a list, causing
best_match to always fall back to the default locale.

Usage:
    uvicorn app:APP --host 0.0.0.0 --port 8080 --app-dir /pygeoapi/pygeoapi-swissgeo-extensions
"""

import asyncio
from collections.abc import Callable

import pygeoapi.starlette_app as _starlette_mod
from pygeoapi.api import API, APIRequest
from pygeoapi.starlette_app import APP as _PYGEOAPI_APP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from swissgeo_provider import set_request_params

_original_call_api_threadsafe = _starlette_mod.call_api_threadsafe


def _call_api_threadsafe_with_lang(
  loop: asyncio.AbstractEventLoop,
  api_function: Callable,
  actual_api: API,
  api_request: APIRequest,
  *args: object,
) -> tuple:
  set_request_params(
    lang=api_request.params.get("lang", None),
    fmt=api_request.params.get("f", None),
  )
  return _original_call_api_threadsafe(loop, api_function, actual_api, api_request, *args)


_starlette_mod.call_api_threadsafe = _call_api_threadsafe_with_lang  # ty: ignore[invalid-assignment]


async def _redirect_to_api(_request: Request) -> RedirectResponse:
  return RedirectResponse(url="/api/oar/rc1")


APP = Starlette(
  routes=[
    Route("/", _redirect_to_api),
    Mount("/api/oar/rc1", app=_PYGEOAPI_APP),
  ]
)
