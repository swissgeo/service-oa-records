"""Tests for swissgeo_provider helper functions."""

import copy
import sys
import threading
from pathlib import Path

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


def _localized_mapping(record_fields: dict) -> dict:
  """Build an index mapping in the localized layout written by service-control."""
  subtree = {"dynamic": False, "properties": {"properties": {"properties": record_fields}}}
  return {
    "properties": {
      "id": {"type": "keyword"},
      "type": {"type": "keyword", "index": False},
      **dict.fromkeys(("de", "fr", "it", "rm", "en"), subtree),
    },
  }


_SORT_FIELDS = {"sort": {"type": "keyword", "normalizer": "sort_normalizer"}}

_DATASET_MAPPING = _localized_mapping(
  {
    "type": {"type": "keyword", "index": False},
    "title": {"type": "search_as_you_type", "fields": _SORT_FIELDS},
    "description": {"type": "search_as_you_type"},
    "concepts": {"type": "keyword"},
    "preferredDistributionId": {"type": "keyword", "index": False},
  },
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

    monkeypatch.setattr(swissgeo_provider, "_create_client", fake_create_client)
    monkeypatch.setattr(SwissGeoProvider, "_fetch_mapping", lambda _provider: _DATASET_MAPPING)

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
    assert second.get_fields()["title"] == {"type": "string"}

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
    def failing_fetch_mapping(_provider) -> dict:
      raise KeyError("mappings")

    monkeypatch.setattr(SwissGeoProvider, "_fetch_mapping", failing_fetch_mapping)
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


def _make_provider(resource_id="col", mapping=_DATASET_MAPPING) -> SwissGeoProvider:
  """Build a SwissGeoProvider without touching the real parent __init__."""
  provider = object.__new__(SwissGeoProvider)
  provider.resource_id = resource_id
  provider.index_name = "idx"
  provider._fields = {}
  # setattr: an instance attribute stands in for the method here.
  setattr(provider, "_fetch_mapping", lambda: mapping)  # noqa: B010
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

  def test_sets_request_language(self, monkeypatch) -> None:
    provider = _make_provider("col")
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      lambda _self, **_kwargs: {"features": []},
    )
    set_request_params(lang="fr", fmt=None)

    provider.query()

    assert provider.lang == "fr"
    assert provider.mask_prop("concepts") == "fr.properties.concepts"


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
    assert provider.lang == "de"

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

  def test_title_rewritten_to_sort_subfield(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "title", "order": "+"}]

    assert provider._resolve_sortby(sortby) == [{"property": "title.sort", "order": "+"}]

  def test_descending_order_preserved(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "title", "order": "-"}]

    assert provider._resolve_sortby(sortby) == [{"property": "title.sort", "order": "-"}]

  def test_any_field_with_sort_subfield_rewritten(self) -> None:
    provider = _make_provider(
      mapping=_localized_mapping({"name": {"type": "search_as_you_type", "fields": _SORT_FIELDS}}),
    )

    resolved = provider._resolve_sortby([{"property": "name", "order": "+"}])

    assert resolved == [{"property": "name.sort", "order": "+"}]

  def test_text_field_without_sort_subfield_rejected(self) -> None:
    provider = _make_provider()

    with pytest.raises(swissgeo_provider.ProviderInvalidQueryError):
      provider._resolve_sortby([{"property": "description", "order": "+"}])

  def test_keyword_properties_left_untouched(self) -> None:
    provider = _make_provider()
    sortby = [{"property": "concept", "order": "-"}]

    assert provider._resolve_sortby(sortby) == sortby

  def test_empty_sortby_returned_as_is(self) -> None:
    provider = _make_provider()

    assert provider._resolve_sortby([]) == []

  def test_query_passes_resolved_sortby_to_parent(self, monkeypatch) -> None:
    captured = {}

    def fake_query(_self, **kwargs) -> dict:
      captured.update(kwargs)
      return {"features": []}

    provider = _make_provider()
    monkeypatch.setattr(swissgeo_provider.OpenSearchCatalogueProvider, "query", fake_query)
    set_request_params(lang="de", fmt=None)

    provider.query(sortby=[{"property": "title", "order": "+"}], language="de")

    assert captured["sortby"] == [{"property": "title.sort", "order": "+"}]
    assert provider.mask_prop("title.sort") == "de.properties.title.sort"


class TestGetFields:
  def test_reads_fields_from_language_subtree(self) -> None:
    provider = _make_provider()

    fields = provider.get_fields()

    assert fields == {
      "type": {"type": "keyword"},
      "title": {"type": "string"},
      "title.sort": {"type": "keyword"},
      "description": {"type": "string"},
      "concept": {"type": "keyword"},
      "preferredDistributionId": {"type": "keyword"},
      "q": {"type": "string"},
    }

  def test_registers_concepts_as_concept(self) -> None:
    provider = _make_provider()

    fields = provider.get_fields()

    assert fields["concept"] == {"type": "keyword"}
    assert "concepts" not in fields

  def test_translates_date_and_number_types(self) -> None:
    provider = _make_provider(
      mapping=_localized_mapping({"created": {"type": "date"}, "size": {"type": "long"}}),
    )

    fields = provider.get_fields()

    assert fields["created"] == {"type": "string", "format": "date"}
    assert fields["size"] == {"type": "number", "format": "long"}

  def test_skips_object_fields_without_type(self) -> None:
    provider = _make_provider(
      mapping=_localized_mapping({"title": {"type": "text"}, "links": {"properties": {"href": {"type": "keyword"}}}}),
    )

    fields = provider.get_fields()

    assert "links" not in fields
    assert fields["title"] == {"type": "string"}

  def test_cached_fields_not_refetched(self) -> None:
    provider = _make_provider()
    provider._fields = {"cached": {"type": "keyword"}}

    assert provider.get_fields() == {"cached": {"type": "keyword"}}

  def test_old_mapping_layout_raises(self) -> None:
    provider = _make_provider(mapping={"properties": {"properties": {"properties": {}}}})

    with pytest.raises(swissgeo_provider.ProviderQueryError, match="localized mapping"):
      provider.get_fields()


class TestFetchMapping:
  def test_returns_mapping_of_first_index(self) -> None:
    class _Indices:
      def get_mapping(self, index) -> dict:
        assert index == "my-alias"
        return {"my-index-v2": {"mappings": _DATASET_MAPPING}}

    provider = object.__new__(SwissGeoProvider)
    provider.index_name = "my-alias"
    setattr(provider, "os_", type("_Client", (), {"indices": _Indices()})())  # noqa: B010

    assert provider._fetch_mapping() == _DATASET_MAPPING


class TestMaskProp:
  def test_defaults_to_en(self) -> None:
    assert _make_provider().mask_prop("title") == "en.properties.title"

  def test_uses_request_language(self) -> None:
    provider = _make_provider()
    provider.lang = "it"

    assert provider.mask_prop("dataset") == "it.properties.dataset"


# ---------------------------------------------------------------------------
# SwissGeoProvider against a fake OpenSearch client
#
# Runs the real parent query()/get(), so the request bodies show where the
# provider points filters, sorting and search in the localized documents.
# ---------------------------------------------------------------------------


def _localized_doc(record_id: str, titles: dict[str, str]) -> dict:
  """Build a document in the localized layout, with one record per language."""
  return {
    "id": record_id,
    "type": "Feature",
    **{
      lang: {
        "id": record_id,
        "type": "Feature",
        "properties": {"type": "Dataset", "title": title, "concepts": ["location"]},
        "links": [],
      }
      for lang, title in titles.items()
    },
  }


_DOC = _localized_doc("rec-1", {"de": "Wanderwege", "fr": "Chemins pédestres", "en": "Hiking trails"})


class _FakeIndices:
  def __init__(self, mapping: dict) -> None:
    self.mapping = mapping

  def get_mapping(self, index: str) -> dict:
    return {f"{index}-20261007": {"mappings": self.mapping}}


class _FakeOpenSearch:
  def __init__(self, mapping: dict, docs: list[dict]) -> None:
    self.indices = _FakeIndices(mapping)
    self.docs = docs
    self.searches: list[dict] = []

  def ping(self) -> bool:
    return True

  def search(self, index: str, body: dict, **_kwargs) -> dict:  # noqa: ARG002
    self.searches.append(body)
    hits = [{"_id": doc["id"], "_source": copy.deepcopy(doc)} for doc in self.docs]
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}

  def get(self, index: str, id: str) -> dict:  # noqa: A002, ARG002
    doc = next(doc for doc in self.docs if doc["id"] == id)
    return {"_id": id, "_source": copy.deepcopy(doc)}


class TestProviderAgainstOpenSearch:
  @pytest.fixture(autouse=True)
  def _fake_opensearch(self, monkeypatch) -> None:
    monkeypatch.setattr(swissgeo_provider, "_shared_state", {})
    _local.__dict__.clear()
    self.client = _FakeOpenSearch(_DATASET_MAPPING, [_DOC])
    monkeypatch.setattr(swissgeo_provider, "_create_client", lambda *_: self.client)

  @staticmethod
  def _provider() -> SwissGeoProvider:
    return SwissGeoProvider(
      {
        "name": "swissgeo_provider.SwissGeoProvider",
        "type": "record",
        "data": "http://opensearch:9200/swissgeo-catalog",
        "resource_id": "swissgeo-catalog",
        "id_field": "externalId",
        "title_field": "title",
      },
    )

  def _query(self, lang: str, **kwargs) -> tuple[dict, dict]:
    """Run a query in *lang*; return the result and the body sent to OpenSearch."""
    set_request_params(lang=lang, fmt="json")
    result = self._provider().query(**kwargs)
    return result, self.client.searches[-1]

  def test_returns_record_of_request_language(self) -> None:
    result, _ = self._query("fr")

    assert result["numberMatched"] == 1
    feature = result["features"][0]
    assert feature["properties"]["title"] == "Chemins pédestres"
    assert feature["links"][0]["href"].endswith("/collections/swissgeo-catalog/items/rec-1?lang=fr&f=json")

  def test_sorts_on_sort_subfield_of_request_language(self) -> None:
    _, body = self._query("fr", sortby=[{"property": "title", "order": "+"}])

    assert body["sort"] == [{"fr.properties.title.sort": {"order": "asc"}}]

  def test_descending_sort(self) -> None:
    _, body = self._query("de", sortby=[{"property": "title", "order": "-"}])

    assert body["sort"] == [{"de.properties.title.sort": {"order": "desc"}}]

  def test_filters_on_request_language_subtree(self) -> None:
    _, body = self._query("it", properties=[("concept", "location")], select_properties=[], bbox=[])

    assert body["query"]["bool"]["filter"] == [
      {"match": {"it.properties.concepts": {"query": "location", "minimum_should_match": "100%"}}},
    ]

  def test_free_text_search_spans_all_languages(self) -> None:
    _, body = self._query("fr", q="wander")

    # No ``fields``: OpenSearch searches every indexed field, i.e. all languages.
    assert body["query"]["bool"]["must"] == {"query_string": {"query": "wander"}}

  def test_unsupported_sort_rejected_before_search(self) -> None:
    set_request_params(lang="de", fmt=None)

    with pytest.raises(swissgeo_provider.ProviderInvalidQueryError):
      self._provider().query(sortby=[{"property": "description", "order": "+"}])
    assert self.client.searches == []

  def test_get_returns_record_of_request_language(self) -> None:
    set_request_params(lang="en", fmt=None)

    result = self._provider().get("rec-1")

    assert result is not None
    assert result["properties"]["title"] == "Hiking trails"
    assert result["links"] == []

  def test_old_mapping_layout_fails_at_startup(self) -> None:
    self.client.indices.mapping = {"properties": {"properties": {"properties": {}}}}

    with pytest.raises(swissgeo_provider.ProviderQueryError, match="localized mapping"):
      self._provider()
