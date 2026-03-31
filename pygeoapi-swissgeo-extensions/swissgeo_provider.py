"""
SwissGeo OpenSearch catalogue provider for OGC API Records.

Extends OpenSearchCatalogueProvider with language-aware field selection:
when a locale is requested via the ``language`` kwarg, ``title`` and
``description`` are transparently swapped for their per-language variants
(``title_de``, ``title_fr``, …) before handing results back to pygeoapi.

Also patches same-host links to carry ``lang`` and ``f`` query params.

Usage in pygeoapi-config.yml:
    providers:
      - type: record
        name: swissgeo_provider.SwissGeoProvider
        data: http://opensearch:9200/swissgeo-catalog
        id_field: externalId
        time_field: recordCreated
        title_field: title
        languages:
          - en
          - de
          - fr
          - it
"""

import logging
import os
import threading
from urllib.parse import urlencode, urlparse

from pygeoapi.provider.opensearch_ import OpenSearchCatalogueProvider

LOGGER = logging.getLogger(__name__)

_SUPPORTED_LANGS = {"de", "en", "fr", "it"}

_local = threading.local()


def set_request_params(lang: str | None, fmt: str | None) -> None:
    """Called by app.py in the executor thread before the API call."""
    _local.lang = lang
    _local.fmt = fmt


def _get_lang_and_fmt() -> tuple[str, str | None]:
    """Read lang and fmt from thread-local (Starlette) or Flask request args."""
    lang = getattr(_local, "lang", None)
    fmt = getattr(_local, "fmt", None)
    if lang is None:
        try:
            from flask import request as flask_request

            lang = flask_request.args.get("lang", "")
            fmt = fmt or flask_request.args.get("f", None)
        except RuntimeError:
            pass
    if not lang:
        return "en", fmt
    primary = lang.split("-")[0].split("_")[0].lower()
    return (primary if primary in _SUPPORTED_LANGS else "en"), fmt


class SwissGeoProvider(OpenSearchCatalogueProvider):
    """
    OGC API Records provider backed by OpenSearch.

    Adds language-aware title/description field selection and same-host
    link patching on top of the standard OpenSearchCatalogueProvider.
    """

    def __init__(self, provider_def):
        LOGGER.info("SwissGeoProvider.__init__ called:")
        super().__init__(provider_def)
        self.resource_id = provider_def.get("resource_id", self.name)

    def query(
        self,
        offset=0,
        limit=10,
        resulttype="results",
        bbox=[],
        datetime_=None,
        properties=[],
        sortby=[],
        select_properties=[],
        skip_geometry=False,
        q=None,
        filterq=None,
        **kwargs,
    ):
        lang, fmt = _get_lang_and_fmt()
        LOGGER.debug("SwissGeoProvider.query lang=%s fmt=%s", lang, fmt)

        result = super().query(
            offset=offset,
            limit=limit,
            resulttype=resulttype,
            bbox=bbox,
            datetime_=datetime_,
            properties=properties,
            sortby=sortby,
            select_properties=select_properties,
            skip_geometry=skip_geometry,
            q=q,
            filterq=filterq,
            **kwargs,
        )

        for feature in result.get("features", []):
            _apply_lang(feature["properties"], lang)
            links = feature.setdefault("links", [])
            _ensure_self_link(links, self.resource_id, feature.get("id", ""))
            _patch_links(links, lang, fmt)
            for record in feature.get("records", []):
                _patch_links(record.get("links", []), lang, fmt)

        return result

    def get(self, identifier, **kwargs):
        lang, fmt = _get_lang_and_fmt()
        LOGGER.debug(
            "SwissGeoProvider.get identifier=%s lang=%s fmt=%s", identifier, lang, fmt
        )

        result = super().get(identifier, **kwargs)

        if result:
            _apply_lang(result["properties"], lang)
            links = result.setdefault("links", [])
            _ensure_self_link(links, self.resource_id, identifier)
            _patch_links(links, lang, fmt)
            for record in result.get("records", []):
                _patch_links(record.get("links", []), lang, fmt)

        return result


def _apply_lang(props: dict, lang: str) -> None:
    """
    Overwrite ``title`` and ``description`` with their localised variants
    if the variant exists and is non-empty, then strip all per-lang fields.
    """
    for field in ("title", "description"):
        localised = props.get(f"{field}_{lang}", "")
        if localised:
            props[field] = localised

    for language in _SUPPORTED_LANGS:
        for field in ("title", "description"):
            props.pop(f"{field}_{language}", None)


_SERVER_URL = os.environ.get("PYGEOAPI_SERVER_URL", "").rstrip("/")


def _ensure_self_link(links: list, collection_id: str, item_id: str) -> None:
    """Insert a ``rel=self`` link if none is present in *links*."""
    if any(link.get("rel") == "self" for link in links):
        return
    if not item_id:
        return
    href = f"/collections/{collection_id}/items/{item_id}"
    if _SERVER_URL:
        href = f"{_SERVER_URL}{href}"
    links.insert(
        0,
        {
            "href": href,
            "rel": "self",
            "type": "application/geo+json",
        },
    )


def _patch_links(links: list, lang: str, fmt: str | None) -> None:
    """
    Append ``lang`` (and ``f`` if present) to relative links and links
    starting with PYGEOAPI_SERVER_URL. External links are left untouched.
    """
    params = {"lang": lang}
    if fmt:
        params["f"] = fmt
    qs = urlencode(params)

    for link in links:
        href = link.get("href", "")
        if not href:
            continue
        parsed = urlparse(href)
        is_relative = not parsed.scheme
        is_same_host = _SERVER_URL and href.startswith(_SERVER_URL)
        if is_relative or is_same_host:
            if is_relative and _SERVER_URL:
                href = f"{_SERVER_URL}{href}"
            sep = "&" if "?" in href else "?"
            link["href"] = f"{href}{sep}{qs}"
