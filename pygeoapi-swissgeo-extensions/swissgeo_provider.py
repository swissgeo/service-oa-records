"""SwissGeo OpenSearch catalogue provider for OGC API Records.

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

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from pygeoapi.provider.opensearch_ import OpenSearchCatalogueProvider
from requests_aws4auth import AWS4Auth

LOGGER = logging.getLogger(__name__)

_SUPPORTED_LANGS = {"de", "en", "fr", "it"}

_local = threading.local()


def set_request_params(
    lang: str | None,
    fmt: str | None,
    server_url: str | None = None,
) -> None:
    """Set lang, fmt, and server_url on the current thread-local before an API call."""
    _local.lang = lang
    _local.fmt = fmt
    _local.server_url = server_url


def _get_lang_and_fmt() -> tuple[str, str | None]:
    """Read lang and fmt from thread-local (Starlette) or Flask request args."""
    lang = getattr(_local, "lang", None)
    fmt = getattr(_local, "fmt", None)
    if lang is None:
        try:
            from flask import request as flask_request  # noqa: PLC0415

            lang = flask_request.args.get("lang", "")
            fmt = fmt or flask_request.args.get("f", None)
        except RuntimeError as e:
            LOGGER.debug("Could not read lang/fmt from Flask request context: %s", e)
    if not lang:
        return "en", fmt
    primary = lang.split("-")[0].split("_")[0].lower()
    return (primary if primary in _SUPPORTED_LANGS else "en"), fmt


def _get_server_url() -> str:
    """Return the server base URL for the current request.

    Priority: thread-local (set by app.py from Host header) →
    Flask request host_url → empty string (links stay relative).
    """
    server_url = getattr(_local, "server_url", None)
    if server_url is not None:
        return server_url.rstrip("/")
    try:
        from flask import request as flask_request  # noqa: PLC0415

        return flask_request.host_url.rstrip("/")
    except RuntimeError as e:
        LOGGER.debug("Could not read server_url from Flask request context: %s", e)
    return ""


class SwissGeoProvider(OpenSearchCatalogueProvider):
    """OGC API Records provider backed by OpenSearch.

    Adds language-aware title/description field selection and same-host
    link patching on top of the standard OpenSearchCatalogueProvider.
    """

    def __init__(self, provider_def: dict) -> None:  # noqa: D107
        LOGGER.info("SwissGeoProvider.__init__ called:")
        if str(provider_def.get("aws4auth", "false")).lower() == "true":
            self._inject_aws4auth(provider_def)  # calls super() internally
        else:
            super().__init__(provider_def)
        self.resource_id = provider_def.get("resource_id", self.name)

    def _inject_aws4auth(self, provider_def: dict) -> None:
        """Monkey-patch OpenSearch in the parent module.

        Ensures super().__init__() builds an AWS4Auth-authenticated client
        instead of an unauthenticated one.
        """
        import pygeoapi.provider.opensearch_ as _os_mod  # noqa: PLC0415

        region = provider_def.get(
            "aws_region", os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
        )
        service = provider_def.get("aws_service", "es")
        LOGGER.info(
            "Configuring AWS SigV4 auth (region=%s service=%s)", region, service,
        )
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            service,
            session_token=credentials.token,
        )

        _original_opensearch = _os_mod.OpenSearch

        def _aws_opensearch(host, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            return OpenSearch(
                hosts=[host],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
            )

        _os_mod.OpenSearch = _aws_opensearch  # ty: ignore[invalid-assignment]
        try:
            super().__init__(provider_def)
        finally:
            _os_mod.OpenSearch = _original_opensearch

    def query(  # noqa: ANN201, PLR0913
        self,
        offset: int = 0,
        limit: int = 10,
        resulttype: str = "results",
        bbox: list | None = None,
        datetime_: str | None = None,
        properties: list | None = None,
        sortby: list | None = None,
        select_properties: list | None = None,
        skip_geometry: bool = False,  # noqa: FBT001, FBT002
        q: str | None = None,
        filterq: str | None = None,
        **kwargs,  # noqa: ANN003
    ):
        """Execute a catalogue query with language-aware post-processing."""
        if select_properties is None:
            select_properties = []
        if sortby is None:
            sortby = []
        if properties is None:
            properties = []
        if bbox is None:
            bbox = []
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

    def get(self, identifier: str, **kwargs) -> dict | None:  # noqa: ANN003
        """Fetch a single record by ID with language-aware post-processing."""
        lang, fmt = _get_lang_and_fmt()
        LOGGER.debug(
            "SwissGeoProvider.get identifier=%s lang=%s fmt=%s",
            identifier,
            lang,
            fmt,
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
    """Overwrite ``title`` and ``description`` with localised variants.

    If the per-language variant exists and is non-empty it replaces the
    generic field, then all per-lang fields are stripped.
    """
    for field in ("title", "description"):
        localised = props.get(f"{field}_{lang}", "")
        if localised:
            props[field] = localised

    for language in _SUPPORTED_LANGS:
        for field in ("title", "description"):
            props.pop(f"{field}_{language}", None)


def _ensure_self_link(links: list, collection_id: str, item_id: str) -> None:
    """Insert a ``rel=self`` link if none is present in *links*."""
    if any(link.get("rel") == "self" for link in links):
        return
    if not item_id:
        return
    server_url = _get_server_url()
    href = f"/collections/{collection_id}/items/{item_id}"
    if server_url:
        href = f"{server_url}{href}"
    links.insert(
        0,
        {
            "href": href,
            "rel": "self",
            "type": "application/geo+json",
        },
    )


def _patch_links(links: list, lang: str, fmt: str | None) -> None:
    """Append ``lang`` (and ``f`` if present) to same-host and relative links.

    External links are left untouched.
    """
    params: dict[str, str] = {"lang": lang}
    if fmt:
        params["f"] = fmt
    qs = urlencode(params)
    server_url = _get_server_url()

    for link in links:
        href = link.get("href", "")
        if not href:
            continue
        parsed = urlparse(href)
        is_relative = not parsed.scheme
        is_same_host = server_url and href.startswith(server_url)
        if is_relative or is_same_host:
            if is_relative and server_url:
                href = f"{server_url}{href}"
            sep = "&" if "?" in href else "?"
            link["href"] = f"{href}{sep}{qs}"
