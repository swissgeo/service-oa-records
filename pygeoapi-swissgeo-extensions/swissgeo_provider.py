"""SwissGeo OpenSearch catalogue provider for OGC API Records.

Extends OpenSearchCatalogueProvider with language-aware field selection:
``title`` and ``description`` arrive as nested per-language objects
(``{"de": …, "fr": …}``) and are collapsed to the language pygeoapi resolves
(passed in via the ``language`` kwarg) using ``pygeoapi.l10n.translate``,
before handing results back to pygeoapi.

Also patches same-host links to carry ``lang`` and ``f`` query params, and
applies a default ordering when the client sends no ``sortby``: alphabetical
by localised title when browsing, relevance when searching, always with ``id``
as a tiebreaker so offset paging is stable. This relies on the ``raw`` keyword
sub-field of ``properties.title.<lang>`` in the index mapping — see the Sorting
section of README.md.

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
from opentelemetry import trace
from pygeoapi import l10n
from pygeoapi.provider.opensearch_ import OpenSearchCatalogueProvider

LOGGER = logging.getLogger(__name__)

_tracer = trace.get_tracer(__name__)

_SUPPORTED_LANGS = {"de", "en", "fr", "it"}

# Fields the parent provider looks up under ``properties.`` via mask_prop().
# ``id`` lives at the document root and ``_score`` is OpenSearch metadata, so
# neither may be prefixed.
_ROOT_FIELDS = {"id", "_score"}

# Relevance, used as the primary key for free-text searches.
_SCORE_FIELD = "_score"

# Secondary sort key. Titles are not unique, and OpenSearch gives no ordering
# guarantee between documents that tie on the primary key, which would make
# offset-based paging skip or repeat records.
_TIEBREAK_FIELD = "id"

# Sortable leaf fields exposed to clients, e.g. ``?sortby=-title.fr``.
_TITLE_FIELD = "title"

# Styles are served outside the records API prefix, so relative links to them
# are resolved against the hostname instead of the base URL.
_STYLES_PREFIX = "/api/oas/v0/styles"

_local = threading.local()


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


def _sort_lang(language, fallback: str) -> str:  # noqa: ANN001
  """Resolve *language* to a supported primary language code.

  ``language`` is whatever pygeoapi negotiated (a ``babel.Locale`` or a string);
  *fallback* is the already-validated code from the thread-local. Sorting must
  use the same language as :func:`_translate_props` so the order matches the
  titles that are actually rendered.
  """
  if not language:
    return fallback
  primary = str(language).split("-")[0].split("_")[0].lower()
  return primary if primary in _SUPPORTED_LANGS else fallback


def _get_hostname() -> str:
  """Return the server hostname set by app.py, or fall back to the default."""
  return os.environ.get("PYGEOAPI_HOSTNAME", "http://localhost:8080")


def _get_base_url() -> str:
  """Return the server base URL set by app.py, or fall back to env vars."""
  return f"{_get_hostname()}{os.environ.get('API_PREFIX', '/api/oar/rc1')}"


class SwissGeoProvider(OpenSearchCatalogueProvider):
  """OGC API Records provider backed by OpenSearch.

  Adds language-aware title/description field selection and same-host
  link patching on top of the standard OpenSearchCatalogueProvider.
  """

  @_tracer.start_as_current_span("SwissGeoProvider.__init__")
  def __init__(self, provider_def: dict) -> None:
    LOGGER.info("SwissGeoProvider.__init__ called")
    if str(provider_def.get("aws4auth", "false")).lower() == "true":
      with _aws4auth.patched_opensearch(provider_def):
        super().__init__(provider_def)
    else:
      super().__init__(provider_def)
    self.resource_id = provider_def.get("resource_id", self.name)

  def get_fields(self) -> dict:
    """Register the per-language title fields and ``id`` as queryable.

    The parent only picks up keys directly under ``properties`` that carry a
    ``type``. ``title`` is an object of language sub-fields and therefore never
    appears, so neither pygeoapi's ``sortby`` validation nor its sort-clause
    builder can resolve it. Declaring the leaves explicitly makes
    ``?sortby=title.de`` (and the default sort below) work.

    ``string`` is deliberate: it is what makes the parent target the sortable
    ``.raw`` sub-field defined in the index mapping. ``id`` is a ``keyword``
    so it is sorted on directly.

    The result is written back to ``_fields`` because ``BaseProvider.fields`` —
    which pygeoapi consults to validate ``sortby`` — reads that attribute
    directly instead of calling this method.
    """
    fields = super().get_fields()
    for lang in sorted(_SUPPORTED_LANGS):
      fields[f"{_TITLE_FIELD}.{lang}"] = {"type": "string"}
    fields[_TIEBREAK_FIELD] = {"type": "keyword"}
    fields[_SCORE_FIELD] = {"type": "keyword"}
    self._fields = fields
    return fields

  def mask_prop(self, property_name: str) -> str:
    """Resolve a field name to its OpenSearch path.

    The parent prefixes everything with ``properties.``; root-level fields
    such as ``id`` must not be.
    """
    if property_name in _ROOT_FIELDS:
      return property_name
    return super().mask_prop(property_name)

  def _default_sortby(self, lang: str, q: str | None) -> list[dict]:
    """Build the ordering used when the client did not ask for one.

    Browsing is alphabetical by localised title. Free-text searches keep
    relevance first: any explicit ``sort`` makes OpenSearch drop ``_score``
    entirely, which would return search hits in alphabetical order and bury
    the best match.

    Either way ``id`` breaks ties, because OpenSearch gives no ordering
    guarantee between equally-ranked documents and offset paging would
    otherwise skip or repeat records.
    """
    primary = {"property": _SCORE_FIELD, "order": "-"} if q else {"property": f"{_TITLE_FIELD}.{lang}", "order": "+"}
    return [primary, {"property": _TIEBREAK_FIELD, "order": "+"}]

  @_tracer.start_as_current_span("SwissGeoProvider.query")
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
    language = kwargs.get("language")
    lang, fmt = _get_lang_and_fmt()
    if not sortby:
      sortby = self._default_sortby(_sort_lang(language, lang), q)
    LOGGER.debug("SwissGeoProvider.query language=%s fmt=%s sortby=%s", language, fmt, sortby)

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
      _translate_props(feature.get("properties", {}), language)
      links = feature.setdefault("links", [])
      _ensure_self_link(links, self.resource_id, feature.get("id", ""))
      _patch_links(links, lang, fmt)
      for dist in feature.get("features", []):
        _patch_links(dist.get("links", []), lang, fmt)

    return result

  @_tracer.start_as_current_span("SwissGeoProvider.get")
  def get(self, identifier: str, **kwargs) -> dict | None:
    """Fetch a single record by ID with language-aware post-processing."""
    language = kwargs.get("language")
    lang, fmt = _get_lang_and_fmt()
    LOGGER.debug(
      "SwissGeoProvider.get identifier=%s language=%s fmt=%s",
      identifier,
      language,
      fmt,
    )

    result = super().get(identifier, **kwargs)

    if result:
      _translate_props(result.get("properties", {}), language)
      links = result.setdefault("links", [])
      _ensure_self_link(links, self.resource_id, identifier)
      _patch_links(links, lang, fmt)
      for dist in result.get("features", []):
        _patch_links(dist.get("links", []), lang, fmt)

    return result


def _translate_props(props: dict, language) -> None:  # noqa: ANN001
  """Collapse the ``title``/``description`` language structs in place.

  Uses pygeoapi's own :func:`pygeoapi.l10n.translate` so behaviour matches
  the rest of the framework: the value for *language* is returned, falling
  back to the first available language, then to the struct itself. Only the
  known language-struct fields are translated to avoid l10n warnings on
  non-locale sibling keys (``type``, ``rel``, …).
  """
  if not language:
    return
  for field in ("title", "description"):
    if isinstance(props.get(field), dict):
      props[field] = l10n.translate(props[field], language)


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
    is_same_host = base_url and href.startswith(base_url)
    if is_relative or is_same_host:
      if is_relative:
        prefix = _get_hostname() if href.startswith(_STYLES_PREFIX) else base_url
        if prefix:
          href = f"{prefix}{href}"
      sep = "&" if "?" in href else "?"
      link["href"] = f"{href}{sep}{qs}"
