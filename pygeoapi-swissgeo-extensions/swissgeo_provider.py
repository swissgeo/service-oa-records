"""SwissGeo OpenSearch catalogue provider for OGC API Records.

Extends OpenSearchCatalogueProvider with language-aware field selection:
``title`` and ``description`` are transparently swapped for their per-language
variants (``title_de``, ``title_fr``, …) before handing results back to pygeoapi.

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

import aws4auth as _aws4auth
from pygeoapi.provider.opensearch_ import OpenSearchCatalogueProvider

LOGGER = logging.getLogger(__name__)

_SUPPORTED_LANGS = {"de", "en", "fr", "it"}

_local = threading.local()


def set_request_params(
  lang: str | None,
  fmt: str | None,
) -> None:
  """Set lang, fmt, server_url, and url_prefix on the current thread-local before an API call."""
  _local.lang = lang
  _local.fmt = fmt


def _get_lang_and_fmt() -> tuple[str, str | None]:
  """Read lang and fmt from thread-local set by app.py."""
  lang = getattr(_local, "lang", None)
  fmt = getattr(_local, "fmt", None)
  if not lang:
    return "en", fmt
  primary = lang.split("-")[0].split("_")[0].lower()
  return (primary if primary in _SUPPORTED_LANGS else "en"), fmt


def _get_base_url() -> str:
  """Return the server base URL set by app.py, or fall back to env vars."""

  return (
    f"{os.environ.get('PYGEOAPI_HOSTNAME', 'http://localhost:8080')}/{os.environ.get('API_PREFIX', '/api/oar/rc1')}"
  )


class SwissGeoProvider(OpenSearchCatalogueProvider):
  """OGC API Records provider backed by OpenSearch.

  Adds language-aware title/description field selection and same-host
  link patching on top of the standard OpenSearchCatalogueProvider.
  """

  def __init__(self, provider_def: dict) -> None:
    LOGGER.info("SwissGeoProvider.__init__ called")
    if str(provider_def.get("aws4auth", "false")).lower() == "true":
      with _aws4auth.patched_opensearch(provider_def):
        super().__init__(provider_def)
    else:
      super().__init__(provider_def)
    self.resource_id = provider_def.get("resource_id", self.name)

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
    skip_geometry: bool = False,
    q: str | None = None,
    filterq: str | None = None,
    **kwargs,
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

  def get(self, identifier: str, **kwargs) -> dict | None:
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
  base_url = _get_base_url()
  href = f"/collections/{collection_id}/items/{item_id}"
  if base_url:
    href = f"{base_url}{href}"
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
  base_url = _get_base_url()

  for link in links:
    href = link.get("href", "")
    if not href:
      continue
    parsed = urlparse(href)
    is_relative = not parsed.scheme
    is_same_host = base_url and href.startswith(base_url)
    if is_relative or is_same_host:
      if is_relative and base_url:
        href = f"{base_url}{href}"
      sep = "&" if "?" in href else "?"
      link["href"] = f"{href}{sep}{qs}"
