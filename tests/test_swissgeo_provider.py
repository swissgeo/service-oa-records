"""Tests for swissgeo_provider helper functions."""

import sys
import threading
from pathlib import Path

from babel import Locale

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "pygeoapi-swissgeo-extensions"))

import swissgeo_provider
from swissgeo_provider import (
  SwissGeoProvider,
  _ensure_self_link,
  _get_base_url,
  _get_lang_and_fmt,
  _local,
  _patch_links,
  set_request_params,
)

# ---------------------------------------------------------------------------
# set_request_params / _get_lang_and_fmt
# ---------------------------------------------------------------------------


class TestGetLangAndFmt:
  def setup_method(self) -> None:
    # Clear thread-local state before each test
    _local.__dict__.clear()

  def test_defaults_to_en_when_no_lang(self) -> None:
    lang, fmt = _get_lang_and_fmt()
    assert lang == "en"
    assert fmt is None

  def test_supported_lang_returned_as_is(self) -> None:
    set_request_params(lang="de", fmt=None)
    lang, _fmt = _get_lang_and_fmt()
    assert lang == "de"

  @pytest.mark.parametrize("code", ["de", "fr", "it", "en"])
  def test_all_supported_langs(self, code) -> None:
    set_request_params(lang=code, fmt=None)
    lang, _ = _get_lang_and_fmt()
    assert lang == code

  def test_unsupported_lang_falls_back_to_en(self) -> None:
    set_request_params(lang="es", fmt=None)
    lang, _ = _get_lang_and_fmt()
    assert lang == "en"

  def test_locale_tag_stripped_to_primary(self) -> None:
    set_request_params(lang="de-CH", fmt=None)
    lang, _ = _get_lang_and_fmt()
    assert lang == "de"

  def test_underscore_locale_stripped(self) -> None:
    set_request_params(lang="fr_CH", fmt=None)
    lang, _ = _get_lang_and_fmt()
    assert lang == "fr"

  def test_fmt_propagated(self) -> None:
    set_request_params(lang="en", fmt="json")
    _, fmt = _get_lang_and_fmt()
    assert fmt == "json"

  def test_thread_isolation(self) -> None:
    """Each thread gets its own lang/fmt."""
    results = {}

    def run(name, lang) -> None:
      set_request_params(lang=lang, fmt=None)
      results[name] = _get_lang_and_fmt()[0]

    t1 = threading.Thread(target=run, args=("a", "de"))
    t2 = threading.Thread(target=run, args=("b", "fr"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["a"] == "de"
    assert results["b"] == "fr"


# ---------------------------------------------------------------------------
# _ensure_self_link
# ---------------------------------------------------------------------------


class TestEnsureSelfLink:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_inserts_self_link_when_absent(self) -> None:
    links: list = []
    _ensure_self_link(links, "my-collection", "item-1")
    assert len(links) == 1
    assert links[0]["rel"] == "self"
    assert "my-collection/items/item-1" in links[0]["href"]

  def test_does_not_duplicate_self_link(self) -> None:
    links = [{"rel": "self", "href": "http://example.com/existing"}]
    _ensure_self_link(links, "my-collection", "item-1")
    assert len(links) == 1

  def test_skips_when_item_id_empty(self) -> None:
    links: list = []
    _ensure_self_link(links, "my-collection", "")
    assert links == []


# ---------------------------------------------------------------------------
# _patch_links
# ---------------------------------------------------------------------------


class TestPatchLinks:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_appends_lang_to_relative_link(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "de", None)
    assert "lang=de" in links[0]["href"]

  def test_appends_fmt_when_provided(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "fr", "json")
    assert "f=json" in links[0]["href"]

  def test_no_fmt_param_when_fmt_is_none(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "en", None)
    assert "f=" not in links[0]["href"]

  def test_does_not_patch_external_links(self) -> None:
    links = [{"href": "https://external.example.com/resource"}]
    _patch_links(links, "de", None)
    assert "lang=" not in links[0]["href"]

  def test_uses_ampersand_when_query_string_already_present(self) -> None:
    links = [{"href": "/items/1?f=json"}]
    _patch_links(links, "de", None)
    href = links[0]["href"]
    assert href.count("?") == 1
    assert "&lang=de" in href

  def test_skips_link_with_empty_href(self) -> None:
    links = [{"href": ""}]
    _patch_links(links, "de", None)
    assert links[0]["href"] == ""

  def test_prepends_server_url_to_relative_link(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "")

    set_request_params(lang=None, fmt=None)
    links = [{"href": "/collections/col"}]
    _patch_links(links, "en", None)
    assert links[0]["href"].startswith("https://api.example.com/collections/col")

  def test_patches_all_links_in_list(self) -> None:
    links = [
      {"href": "/a"},
      {"href": "/b"},
    ]
    _patch_links(links, "de", None)
    assert "lang=de" in links[0]["href"]
    assert "lang=de" in links[1]["href"]

  def test_empty_link_list_is_noop(self) -> None:
    links: list = []
    _patch_links(links, "de", None)
    assert links == []

  def test_link_without_href_key_skipped(self) -> None:
    links = [{"rel": "self"}]
    _patch_links(links, "de", None)
    assert "href" not in links[0]

  def test_relative_link_without_base_url_left_relative(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "")
    monkeypatch.setenv("API_PREFIX", "")
    set_request_params(lang=None, fmt=None)
    links = [{"href": "/collections/col"}]
    _patch_links(links, "de", None)
    # Still patched (relative), but no host prepended.
    assert links[0]["href"].startswith("/collections/col")
    assert "lang=de" in links[0]["href"]

  def test_styles_link_prepends_hostname_only(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "/api/oar/rc1")

    set_request_params(lang=None, fmt=None)
    links = [{"href": "/api/oas/v0/styles/base"}]
    _patch_links(links, "de", None)
    assert links[0]["href"].startswith("https://api.example.com/api/oas/v0/styles/base")
    assert "lang=de" in links[0]["href"]

  def test_both_lang_and_fmt_appended(self) -> None:
    links = [{"href": "/items/1"}]
    _patch_links(links, "fr", "html")
    href = links[0]["href"]
    assert "lang=fr" in href
    assert "f=html" in href

  def test_adds_content_type_to_relative_link(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "de", "json")
    assert links[0]["type"] == "application/json"

  def test_does_not_adds_content_type_if_no_fmt_given(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "de", None)
    assert "type" not in links[0]

  def test_does_not_adds_content_type_if_unkown_fmt_given(self) -> None:
    links = [{"href": "/collections/col/items/1"}]
    _patch_links(links, "de", "foo")
    assert "type" not in links[0]

  def test_self_link_type_is_geojson_if_json(self) -> None:
    links = [{"href": "/collections/col/items/1", "rel": "self"}]
    _patch_links(links, "de", "json")
    assert links[0]["type"] == "application/geo+json"

  def test_self_link_type_is_not_geojson_if_not_json(self) -> None:
    links = [{"href": "/collections/col/items/1", "rel": "self"}]
    _patch_links(links, "de", "html")
    assert links[0]["type"] == "text/html"


# ---------------------------------------------------------------------------
# _get_base_url
# ---------------------------------------------------------------------------


class TestGetBaseUrl:
  def test_defaults_when_env_unset(self, monkeypatch) -> None:
    monkeypatch.delenv("PYGEOAPI_HOSTNAME", raising=False)
    monkeypatch.delenv("API_PREFIX", raising=False)
    assert _get_base_url() == "http://localhost:8080/api/oar/rc1"

  def test_uses_env_vars(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "/prefix")
    assert _get_base_url() == "https://api.example.com/prefix"

  def test_empty_prefix(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "")
    assert _get_base_url() == "https://api.example.com"


# ---------------------------------------------------------------------------
# SwissGeoProvider.__init__
# ---------------------------------------------------------------------------


class _FakeClient:
  def __init__(self, reachable=True) -> None:
    self.reachable = reachable

  def ping(self) -> bool:
    return self.reachable


class TestProviderInit:
  @pytest.fixture(autouse=True)
  def _fake_opensearch(self, monkeypatch) -> None:
    """Replace the OpenSearch client and mapping lookup, and reset the cache."""
    monkeypatch.setattr(swissgeo_provider, "_shared_state", {})
    self.clients = []

    def fake_create_client(host, _provider_def) -> _FakeClient:
      client = _FakeClient()
      self.clients.append((host, client))
      return client

    def fake_get_fields(provider) -> dict:
      provider._fields.setdefault("keywords", {"type": "keyword"})
      return provider._fields

    monkeypatch.setattr(swissgeo_provider, "_create_client", fake_create_client)
    monkeypatch.setattr(swissgeo_provider.OpenSearchCatalogueProvider, "get_fields", fake_get_fields)

  @staticmethod
  def _provider_def(**kwargs) -> dict:
    return {
      "name": "swissgeo_provider.SwissGeoProvider",
      "type": "record",
      "data": "http://opensearch:9200/my-catalog",
      **kwargs,
    }

  def test_resource_id_defaults_to_name(self) -> None:
    provider = SwissGeoProvider(self._provider_def())
    assert provider.resource_id == "swissgeo_provider.SwissGeoProvider"

  def test_resource_id_from_provider_def(self) -> None:
    provider = SwissGeoProvider(self._provider_def(resource_id="explicit"))
    assert provider.resource_id == "explicit"

  def test_splits_host_and_index(self) -> None:
    provider = SwissGeoProvider(self._provider_def())
    assert provider.os_host == "http://opensearch:9200"
    assert provider.index_name == "my-catalog"
    assert self.clients[0][0] == "http://opensearch:9200"

  def test_client_and_fields_shared_per_index(self) -> None:
    first = SwissGeoProvider(self._provider_def())
    second = SwissGeoProvider(self._provider_def())

    assert len(self.clients) == 1
    assert second.os_ is first.os_
    assert second.get_fields()["keywords"] == {"type": "keyword"}

  def test_separate_client_per_index(self) -> None:
    first = SwissGeoProvider(self._provider_def())
    second = SwissGeoProvider(self._provider_def(data="http://opensearch:9200/other"))

    assert len(self.clients) == 2
    assert second.os_ is not first.os_

  def test_instances_get_own_fields_copy(self) -> None:
    first = SwissGeoProvider(self._provider_def())
    second = SwissGeoProvider(self._provider_def())

    first._fields["extra"] = {"type": "keyword"}

    assert "extra" not in second._fields

  def test_unreachable_opensearch_raises_and_is_not_cached(self, monkeypatch) -> None:
    monkeypatch.setattr(swissgeo_provider, "_create_client", lambda *_: _FakeClient(reachable=False))
    with pytest.raises(swissgeo_provider.ProviderConnectionError):
      SwissGeoProvider(self._provider_def())
    assert swissgeo_provider._shared_state == {}

  def test_field_lookup_error_raises_query_error(self, monkeypatch) -> None:
    def failing_get_fields(_provider) -> dict:
      raise KeyError("mappings")

    monkeypatch.setattr(swissgeo_provider.OpenSearchCatalogueProvider, "get_fields", failing_get_fields)
    with pytest.raises(swissgeo_provider.ProviderQueryError):
      SwissGeoProvider(self._provider_def())
    assert swissgeo_provider._shared_state == {}


class TestCreateClient:
  @pytest.fixture(autouse=True)
  def _capture_opensearch(self, monkeypatch) -> None:
    self.calls = []

    def fake_opensearch(*args, **kwargs) -> object:
      self.calls.append((args, kwargs))
      return object()

    monkeypatch.setattr(swissgeo_provider, "OpenSearch", fake_opensearch)

  def test_plain_client_uses_settings(self) -> None:
    swissgeo_provider._create_client("http://opensearch:9200", {})

    args, kwargs = self.calls[0]
    settings = swissgeo_provider.get_settings()
    assert args == ("http://opensearch:9200",)
    assert "http_auth" not in kwargs
    assert kwargs["timeout"] == settings.opensearch_timeout
    assert kwargs["max_retries"] == settings.opensearch_max_retries
    assert kwargs["pool_maxsize"] == settings.threadpool_max_workers

  def test_provider_def_overrides_timeout_and_retries(self) -> None:
    swissgeo_provider._create_client("http://opensearch:9200", {"timeout": "5", "max_retries": "1"})

    _, kwargs = self.calls[0]
    assert kwargs["timeout"] == 5
    assert kwargs["max_retries"] == 1

  def test_aws4auth_client_is_signed(self, monkeypatch) -> None:
    auth = object()
    monkeypatch.setattr(swissgeo_provider._aws4auth, "aws_auth", lambda _provider_def: auth)

    swissgeo_provider._create_client("https://search.aws", {"aws4auth": "true"})

    _, kwargs = self.calls[0]
    assert kwargs["hosts"] == ["https://search.aws"]
    assert kwargs["http_auth"] is auth
    assert kwargs["connection_class"] is swissgeo_provider.RequestsHttpConnection


# ---------------------------------------------------------------------------
# SwissGeoProvider.query / .get
#
# The parent OpenSearchCatalogueProvider needs a live OpenSearch cluster, so
# tests build the instance without running __init__ and stub the parent's
# query/get to return canned OpenSearch-shaped results. This exercises the
# language-aware post-processing that SwissGeoProvider layers on top.
# ---------------------------------------------------------------------------


def _make_provider(resource_id="col") -> SwissGeoProvider:
  """Build a SwissGeoProvider without touching the real parent __init__."""
  provider = object.__new__(SwissGeoProvider)
  provider.resource_id = resource_id
  return provider


class TestProviderQuery:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_translates_feature_props_and_adds_links(self, monkeypatch) -> None:
    provider = _make_provider("col")
    parent_result = {
      "features": [
        {
          "de": {"id": "rec-1", "properties": {"title": "Titel"}},
          "fr": {"id": "rec-1", "properties": {"title": "Titre"}},
        },
      ],
    }
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      lambda _self, **_kwargs: parent_result,
    )
    set_request_params(lang="de", fmt=None)

    result = provider.query(language="de")

    feature = result["features"][0]
    assert feature["properties"]["title"] == "Titel"
    self_links = [link for link in feature["links"] if link["rel"] == "self"]
    assert len(self_links) == 1
    assert "col/items/rec-1" in self_links[0]["href"]
    assert all("lang=de" in link["href"] for link in feature["links"])

  def test_empty_result_returned_unchanged(self, monkeypatch) -> None:
    provider = _make_provider("col")
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      lambda _self, **_kwargs: {"features": []},
    )
    set_request_params(lang="en", fmt=None)

    result = provider.query()

    assert result == {"features": []}

  def test_none_kwargs_normalised_before_parent(self, monkeypatch) -> None:
    """query() passes lists (not None) for the collection args to the parent."""
    captured = {}

    def fake_query(_self, **kwargs) -> dict:
      captured.update(kwargs)
      return {"features": []}

    provider = _make_provider("col")
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      fake_query,
    )
    set_request_params(lang="en", fmt=None)

    provider.query()

    assert captured["select_properties"] == []
    assert captured["sortby"] == []
    assert captured["properties"] == []
    assert captured["bbox"] == []

  def test_rewrites_concept_query_param_to_concepts(self, monkeypatch) -> None:
    provider = _make_provider("col")
    captured = {}

    def mocked_query(self, *args, **kwargs) -> dict:  # noqa: ARG001
      captured.update(kwargs)
      return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      mocked_query,
    )

    provider.query(properties=[("concept", "xyz")])

    assert captured["properties"] == [("concepts", "xyz")]


class TestProviderGet:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_translates(self, monkeypatch) -> None:
    provider = _make_provider("col")
    parent_result = {
      "de": {"id": "rec-1", "properties": {"description": "Beschreibung"}},
      "en": {"id": "rec-1", "properties": {"description": "Description"}},
    }
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get",
      lambda _self, _identifier, **_kwargs: parent_result,
    )
    set_request_params(lang="de", fmt=None)

    result = provider.get("rec-1", language="de")

    assert result is not None
    assert result["properties"]["description"] == "Beschreibung"

  def test_none_result_returned_as_is(self, monkeypatch) -> None:
    provider = _make_provider("col")
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get",
      lambda _self, _identifier, **_kwargs: None,
    )
    set_request_params(lang="en", fmt=None)

    assert provider.get("missing") is None


class TestResolveSortby:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_title_rewritten_to_negotiated_language_subfield(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "title", "order": "+"}]

    resolved = provider._resolve_sortby(sortby, Locale("de"))

    assert resolved == [{"property": "title.de.sort", "order": "+"}]

  def test_descending_order_preserved(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "title", "order": "-"}]

    resolved = provider._resolve_sortby(sortby, Locale("fr"))

    assert resolved == [{"property": "title.fr.sort", "order": "-"}]

  def test_unsupported_locale_falls_back_to_en(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "title", "order": "+"}]

    resolved = provider._resolve_sortby(sortby, Locale("es"))

    assert resolved == [{"property": "title.en.sort", "order": "+"}]

  def test_falls_back_to_request_lang_when_language_absent(self) -> None:
    provider = _make_provider()
    set_request_params(lang="it", fmt=None)

    resolved = provider._resolve_sortby([{"property": "title", "order": "+"}], None)

    assert resolved == [{"property": "title.it.sort", "order": "+"}]

  def test_other_properties_left_untouched(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "recordCreated", "order": "-"}]

    resolved = provider._resolve_sortby(sortby, Locale("de"))

    assert resolved == sortby

  def test_empty_sortby_returned_as_is(self) -> None:
    provider = _make_provider()

    assert provider._resolve_sortby([], Locale("de")) == []

  def test_query_passes_resolved_sortby_to_parent(self, monkeypatch) -> None:
    captured = {}

    def fake_query(_self, **kwargs) -> dict:
      captured.update(kwargs)
      return {"features": []}

    provider = _make_provider()
    monkeypatch.setattr(swissgeo_provider.OpenSearchCatalogueProvider, "query", fake_query)
    set_request_params(lang="de", fmt=None)

    provider.query(sortby=[{"property": "title", "order": "+"}], language="de")

    assert captured["sortby"] == [{"property": "title.de.sort", "order": "+"}]


class TestGetFields:
  def test_registers_title_and_language_sort_subfields(self, monkeypatch) -> None:
    provider = _make_provider()
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get_fields",
      lambda _self: {"keywords": {"type": "keyword"}},
    )

    fields = provider.get_fields()

    assert fields["title"] == {"type": "keyword"}
    assert fields["keywords"] == {"type": "keyword"}
    for lang in ("de", "en", "fr", "it"):
      assert fields[f"title.{lang}.sort"] == {"type": "keyword"}

  def test_registers_concepts_as_concept(self, monkeypatch) -> None:
    provider = _make_provider()
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get_fields",
      lambda _self: {"concepts": {"type": "keyword"}},
    )

    fields = provider.get_fields()

    assert fields["concept"] == {"type": "keyword"}
    assert "concepts" not in fields
