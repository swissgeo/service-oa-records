"""SwissGeo OpenSearch catalogue provider for OGC API Records.

Extends OpenSearchCatalogueProvider for the localized index layout written
by service-control: each document holds one complete record per language
(``{"id": …, "type": …, "de": {record}, "fr": {record}, …}``), and only the
fields inside ``<lang>.properties`` are indexed.

The request language (``?lang=``, see app.py) selects the record subtree
that is returned, filtered and sorted (see :meth:`SwissGeoProvider.mask_prop`).
Free-text search (``?q=``) matches the records of all languages.

Also patches same-host links to carry ``lang`` and ``f`` query params.

Supports ``?sortby=<field>`` for fields that carry a ``sort`` keyword
subfield in the index (``title``, ``name``, ``acronym``).

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

from __future__ import annotations

import logging
import os
import threading
from urllib.parse import urlencode, urlparse

import aws4auth as _aws4auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from opentelemetry import trace
from pygeoapi.api import F_JSON, FORMAT_TYPES
from pygeoapi.provider.base import (
  BaseProvider,
  ProviderConnectionError,
  ProviderInvalidQueryError,
  ProviderQueryError,
)
from pygeoapi.provider.opensearch_ import OpenSearchCatalogueProvider
from settings import get_settings

LOGGER = logging.getLogger(__name__)

_tracer = trace.get_tracer(__name__)

_SUPPORTED_LANGS = {"de", "en", "fr", "it"}

# The field definitions are identical in every language subtree of the
# mapping, so they are read from this one.
_MAPPING_LANG = "de"

# Keyword subfield the mapping adds to text fields that can be sorted on.
_SORT_SUBFIELD = "sort"

# Mapping types pygeoapi has to advertise as JSON Schema strings.
_TEXT_TYPES = {"text", "search_as_you_type"}

# Styles are served outside the records API prefix, so relative links to them
# are resolved against the hostname instead of the base URL.
_STYLES_PREFIX = "/api/oas/v0/styles"

_local = threading.local()

# OpenSearch client and field definitions per ``data`` URL, shared by all
# provider instances (see SwissGeoProvider.__init__).
_shared_state: dict[str, tuple[OpenSearch, dict]] = {}
_shared_state_lock = threading.Lock()


def set_request_params(
  lang: str | None,
  fmt: str | None,
) -> None:
  """Set lang, fmt on the current thread-local before an API call."""
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


def _get_hostname() -> str:
  """Return the server hostname set by app.py, or fall back to the default."""
  return os.environ.get("PYGEOAPI_HOSTNAME", "http://localhost:8080")


def _get_base_url() -> str:
  """Return the server base URL set by app.py, or fall back to env vars."""
  return f"{_get_hostname()}{os.environ.get('API_PREFIX', '/api/oar/rc1')}"


def _create_client(host: str, provider_def: dict) -> OpenSearch:
  """Create an OpenSearch client, signed with AWS SigV4 if ``aws4auth`` is set."""
  settings = get_settings()
  timeout = int(provider_def.get("timeout", settings.opensearch_timeout))
  max_retries = int(provider_def.get("max_retries", settings.opensearch_max_retries))
  if str(provider_def.get("aws4auth", "false")).lower() == "true":
    return OpenSearch(
      hosts=[host],
      http_auth=_aws4auth.aws_auth(provider_def),
      use_ssl=True,
      verify_certs=True,
      connection_class=RequestsHttpConnection,
      timeout=timeout,
      max_retries=max_retries,
      retry_on_timeout=True,
      pool_maxsize=settings.threadpool_max_workers,
    )
  return OpenSearch(
    host,
    verify_certs=False,
    timeout=timeout,
    max_retries=max_retries,
    retry_on_timeout=True,
    pool_maxsize=settings.threadpool_max_workers,
  )


def _field_schema(mapping_type: str) -> dict:
  """Translate an OpenSearch mapping type into pygeoapi's field definition."""
  if mapping_type in _TEXT_TYPES:
    return {"type": "string"}
  if mapping_type == "date":
    return {"type": "string", "format": "date"}
  if mapping_type in {"float", "long"}:
    return {"type": "number", "format": mapping_type}
  return {"type": mapping_type}


class SwissGeoProvider(OpenSearchCatalogueProvider):
  """OGC API Records provider backed by OpenSearch.

  Adds language-aware record selection, filtering and sorting, and
  same-host link patching on top of the standard OpenSearchCatalogueProvider.
  """

  # Language subtree of the documents used for the current request; set by
  # query() and get() (pygeoapi creates a provider instance per request).
  lang = "en"

  @_tracer.start_as_current_span("SwissGeoProvider.__init__")
  def __init__(self, provider_def: dict) -> None:
    # pygeoapi instantiates a new provider for every request. The parent
    # __init__ builds a new OpenSearch client and fetches the index mapping
    # each time, which exhausts memory under load. Only the first instance per
    # index connects; later ones reuse its client and field definitions.
    BaseProvider.__init__(self, provider_def)
    self.select_properties = []
    self.os_host, self.index_name = self.data.rsplit("/", 1)
    self.resource_id = provider_def.get("resource_id", self.name)

    with _shared_state_lock:
      state = _shared_state.get(self.data)
      if state is None:
        state = self._connect(provider_def)
        _shared_state[self.data] = state
    self.os_, fields = state
    # Each instance gets its own copy, so changes to it stay local.
    self._fields = dict(fields)

  def _connect(self, provider_def: dict) -> tuple[OpenSearch, dict]:
    """Create the OpenSearch client and load the field definitions."""
    LOGGER.info("Connecting to OpenSearch index %s", self.index_name)
    self.os_ = _create_client(self.os_host, provider_def)
    if not self.os_.ping():
      msg = f"Cannot connect to OpenSearch: {self.os_host}"
      LOGGER.error(msg)
      raise ProviderConnectionError(msg)
    try:
      self.get_fields()
    except Exception as err:
      LOGGER.exception("Cannot read the fields of index %s", self.index_name)
      raise ProviderQueryError(err) from err
    return self.os_, self._fields

  def get_fields(self) -> dict:
    """Return the fields of a record, read from one language subtree of the mapping.

    The parent expects the fields under a top-level ``properties`` object,
    which the localized index no longer has.

    Text fields with a ``sort`` keyword subfield also register it as
    ``<field>.sort`` (type ``keyword``): the parent looks the resolved sort
    property up in ``self.fields``, and the ``keyword`` type keeps it from
    appending a ``.raw`` suffix that does not exist in the mapping.

    Rename the concepts field to concept so that it gets accepted as query
    parameter. Gets rewritten in the query function to concepts again.
    """
    if self._fields:
      return self._fields

    mapping = self._fetch_mapping()
    try:
      record = mapping["properties"][_MAPPING_LANG]["properties"]["properties"]["properties"]
    except KeyError as err:
      msg = (
        f"Index {self.index_name} does not have the localized mapping "
        f"(no {_MAPPING_LANG}.properties.properties); re-run oar_opensearch_export "
        "in service-control"
      )
      raise ProviderQueryError(msg) from err
    fields = {}
    for name, definition in record.items():
      if "type" not in definition:
        continue
      fields[name] = _field_schema(definition["type"])
      if _SORT_SUBFIELD in definition.get("fields", {}):
        fields[f"{name}.{_SORT_SUBFIELD}"] = {"type": "keyword"}
    if "concepts" in fields:
      fields["concept"] = fields.pop("concepts")
    # Same as OpenSearchCatalogueProvider: ``q`` is advertised as a field.
    fields["q"] = {"type": "string"}

    self._fields = fields
    return self._fields

  def _fetch_mapping(self) -> dict:
    """Return the index mapping; an alias resolves to its (first) index."""
    response = self.os_.indices.get_mapping(index=self.index_name)
    return next(iter(response.values()))["mappings"]

  def mask_prop(self, property_name: str) -> str:
    """Map a record property onto its path in the request language subtree."""
    return f"{self.lang}.properties.{property_name}"

  def _resolve_sortby(self, sortby: list) -> list:
    """Rewrite sorts on text fields onto their ``sort`` keyword subfield.

    Text fields cannot be sorted on directly. Those without a ``sort``
    subfield are rejected, as the parent would sort on a ``.raw`` subfield
    that does not exist in the mapping.
    """
    fields = self.get_fields()
    resolved = []
    for sort in sortby:
      prop = sort["property"]
      sort_field = f"{prop}.{_SORT_SUBFIELD}"
      field = fields.get(prop, {})
      if sort_field in fields:
        resolved.append({**sort, "property": sort_field})
      elif field.get("type") == "string" and "format" not in field:
        msg = f"Cannot sort by text property {prop}"
        raise ProviderInvalidQueryError(msg, user_msg=msg)
      else:
        resolved.append(sort)
    return resolved

  @_tracer.start_as_current_span("SwissGeoProvider.query")
  def query(  # noqa: ANN201, PLR0913, PLR0917
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
    language = kwargs.get("language")
    lang, fmt = _get_lang_and_fmt()
    self.lang = lang
    LOGGER.debug("SwissGeoProvider.query lang=%s language=%s fmt=%s", lang, language, fmt)

    sortby = self._resolve_sortby(sortby)
    LOGGER.debug("SwissGeoProvider.query sortby=%s", sortby)

    properties = [("concepts", value) if key == "concept" else (key, value) for key, value in properties]

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

    features = []
    for original in result.get("features", []):
      feature = original.get(lang, {})
      links = feature.setdefault("links", [])
      _ensure_self_link(links, self.resource_id, feature.get("id", ""))
      _patch_links(links, lang, fmt)
      features.append(feature)
    result["features"] = features

    return result

  @_tracer.start_as_current_span("SwissGeoProvider.get")
  def get(self, identifier: str, **kwargs) -> dict | None:
    """Fetch a single record by ID with language-aware post-processing."""
    language = kwargs.get("language")
    lang, fmt = _get_lang_and_fmt()
    self.lang = lang
    LOGGER.debug(
      "SwissGeoProvider.get identifier=%s lang=%s language=%s fmt=%s",
      identifier,
      lang,
      language,
      fmt,
    )

    result = super().get(identifier, **kwargs)
    result = result.get(lang) if result else None
    if result:
      links = result.setdefault("links", [])
      _patch_links(links, lang, fmt)

    return result


def _ensure_self_link(links: list, collection_id: str, item_id: str) -> None:
  """Insert a ``rel=self`` link if none is present in *links*.

  This is only the case for links of features inside feature collections.
  """
  if any(link.get("rel") == "self" for link in links):
    return
  if not item_id:
    return
  href = f"/collections/{collection_id}/items/{item_id}"
  links.insert(
    0,
    {"href": href, "rel": "self"},
  )


def _patch_links(links: list, lang: str, fmt: str | None) -> None:
  """Append ``lang`` (and ``f`` if present) and add content type to relative links.

  Relative links are made absolute with the base URL, except styles links
  (``_STYLES_PREFIX``) which live outside the records API prefix and are
  resolved against the hostname only.

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
    if is_relative:
      prefix = _get_hostname() if href.startswith(_STYLES_PREFIX) else base_url
      if prefix:
        href = f"{prefix}{href}"
      sep = "&" if "?" in href else "?"
      link["href"] = f"{href}{sep}{qs}"

      if fmt == F_JSON and link.get("rel") == "self":
        link["type"] = "application/geo+json"
      elif fmt in FORMAT_TYPES:
        link["type"] = FORMAT_TYPES[fmt]
