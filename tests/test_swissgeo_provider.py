"""Tests for swissgeo_provider helper functions."""

import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
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
  _sort_lang,
  _translate_props,
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
# _translate_props
# ---------------------------------------------------------------------------


class TestTranslateProps:
  def test_title_collapsed_to_requested_lang(self) -> None:
    props = {"title": {"de": "Deutsch", "fr": "Français"}}
    _translate_props(props, "de")
    assert props["title"] == "Deutsch"

  def test_description_collapsed_to_requested_lang(self) -> None:
    props = {"description": {"de": "Deutsch", "fr": "Français"}}
    _translate_props(props, "fr")
    assert props["description"] == "Français"

  def test_accepts_babel_locale(self) -> None:
    locale = Locale("de")
    props = {"title": {"de": "Deutsch", "fr": "Français"}}
    _translate_props(props, locale)
    assert props["title"] == "Deutsch"

  def test_falls_back_to_first_lang_when_requested_missing(self) -> None:
    props = {"title": {"de": "Deutsch"}}
    _translate_props(props, "it")
    assert props["title"] == "Deutsch"

  def test_no_language_leaves_struct_untouched(self) -> None:
    props = {"title": {"de": "Deutsch", "fr": "Français"}}
    _translate_props(props, None)
    assert props["title"] == {"de": "Deutsch", "fr": "Français"}

  def test_non_dict_field_untouched(self) -> None:
    props = {"title": "plain string"}
    _translate_props(props, "de")
    assert props["title"] == "plain string"

  def test_missing_field_ignored(self) -> None:
    props = {"description": {"de": "Deutsch"}}
    _translate_props(props, "de")
    assert "title" not in props
    assert props["description"] == "Deutsch"

  def test_non_lang_fields_untouched(self) -> None:
    props = {"title": {"en": "T"}, "extra": "keep me"}
    _translate_props(props, "en")
    assert props["extra"] == "keep me"


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

  def test_prepends_server_url_when_available(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "/")

    set_request_params(lang=None, fmt=None)
    links: list = []
    _ensure_self_link(links, "col", "abc")
    assert links[0]["href"].startswith("https://api.example.com")

  def test_self_link_type_is_geojson(self) -> None:
    links: list = []
    _ensure_self_link(links, "col", "xyz")
    assert links[0]["type"] == "application/geo+json"


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

  def test_patches_same_host_link(self, monkeypatch) -> None:
    monkeypatch.setenv("PYGEOAPI_HOSTNAME", "https://api.example.com")
    monkeypatch.setenv("API_PREFIX", "/")

    set_request_params(lang=None, fmt=None)
    links = [{"href": "https://api.example.com/collections/col/items/1"}]
    _patch_links(links, "it", None)
    assert "lang=it" in links[0]["href"]

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


class TestProviderInit:
  def _stub_parent_init(self, monkeypatch) -> None:
    """Stub the parent __init__ so it sets ``name`` without OpenSearch."""

    def fake_init(self, provider_def) -> None:
      self.name = provider_def.get("name", "swissgeo-catalog")

    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "__init__",
      fake_init,
    )

  def test_resource_id_defaults_to_name(self, monkeypatch) -> None:
    self._stub_parent_init(monkeypatch)
    provider = SwissGeoProvider({"name": "my-catalog"})
    assert provider.resource_id == "my-catalog"

  def test_resource_id_from_provider_def(self, monkeypatch) -> None:
    self._stub_parent_init(monkeypatch)
    provider = SwissGeoProvider({"name": "my-catalog", "resource_id": "explicit"})
    assert provider.resource_id == "explicit"

  def test_aws4auth_branch_uses_patched_context(self, monkeypatch) -> None:
    self._stub_parent_init(monkeypatch)
    called = {"patched": False}

    @contextmanager
    def fake_patched(_provider_def) -> Generator[None, None, None]:
      called["patched"] = True
      yield

    monkeypatch.setattr(swissgeo_provider._aws4auth, "patched_opensearch", fake_patched)

    provider = SwissGeoProvider({"name": "cat", "aws4auth": "true"})

    assert called["patched"] is True
    assert provider.resource_id == "cat"

  def test_no_aws4auth_skips_patched_context(self, monkeypatch) -> None:
    self._stub_parent_init(monkeypatch)
    called = {"patched": False}

    @contextmanager
    def fake_patched(_provider_def) -> Generator[None, None, None]:
      called["patched"] = True
      yield

    monkeypatch.setattr(swissgeo_provider._aws4auth, "patched_opensearch", fake_patched)

    SwissGeoProvider({"name": "cat", "aws4auth": "false"})

    assert called["patched"] is False


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
          "id": "rec-1",
          "properties": {"title": {"de": "Titel", "fr": "Titre"}},
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

  def test_patches_distribution_links(self, monkeypatch) -> None:
    provider = _make_provider("col")
    parent_result = {
      "features": [
        {
          "id": "rec-1",
          "properties": {},
          "features": [
            {"links": [{"href": "/dist/1"}]},
          ],
        },
      ],
    }
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "query",
      lambda _self, **_kwargs: parent_result,
    )
    set_request_params(lang="fr", fmt="json")

    result = provider.query(language="fr")

    dist_link = result["features"][0]["features"][0]["links"][0]
    assert "lang=fr" in dist_link["href"]
    assert "f=json" in dist_link["href"]

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
    assert captured["properties"] == []
    assert captured["bbox"] == []


class TestProviderGet:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_translates_and_adds_links(self, monkeypatch) -> None:
    provider = _make_provider("col")
    parent_result = {
      "id": "rec-1",
      "properties": {"description": {"de": "Beschreibung", "en": "Description"}},
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
    self_links = [link for link in result["links"] if link["rel"] == "self"]
    assert len(self_links) == 1
    assert "col/items/rec-1" in self_links[0]["href"]

  def test_patches_distribution_links(self, monkeypatch) -> None:
    provider = _make_provider("col")
    parent_result = {
      "id": "rec-1",
      "properties": {},
      "features": [{"links": [{"href": "/dist/1"}]}],
    }
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get",
      lambda _self, _identifier, **_kwargs: parent_result,
    )
    set_request_params(lang="it", fmt=None)

    result = provider.get("rec-1", language="it")

    assert result is not None
    assert "lang=it" in result["features"][0]["links"][0]["href"]

  def test_none_result_returned_as_is(self, monkeypatch) -> None:
    provider = _make_provider("col")
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get",
      lambda _self, _identifier, **_kwargs: None,
    )
    set_request_params(lang="en", fmt=None)

    assert provider.get("missing") is None


# ---------------------------------------------------------------------------
# _sort_lang
# ---------------------------------------------------------------------------


class TestSortLang:
  def test_falls_back_when_language_missing(self) -> None:
    assert _sort_lang(None, "fr") == "fr"

  def test_falls_back_on_empty_string(self) -> None:
    assert _sort_lang("", "it") == "it"

  @pytest.mark.parametrize("code", ["de", "fr", "it", "en"])
  def test_supported_codes(self, code) -> None:
    assert _sort_lang(code, "en") == code

  def test_accepts_babel_locale(self) -> None:
    assert _sort_lang(Locale("de"), "en") == "de"

  def test_babel_locale_with_territory_stripped(self) -> None:
    assert _sort_lang(Locale("de", "CH"), "en") == "de"

  def test_hyphenated_tag_stripped(self) -> None:
    assert _sort_lang("fr-CH", "en") == "fr"

  def test_unsupported_language_falls_back(self) -> None:
    assert _sort_lang("es", "de") == "de"

  def test_case_insensitive(self) -> None:
    assert _sort_lang("DE", "en") == "de"


# ---------------------------------------------------------------------------
# Default sort ordering
# ---------------------------------------------------------------------------


def _capture_sortby(monkeypatch) -> dict:
  """Stub the parent query and capture the kwargs it receives."""
  captured = {}

  def fake_query(_self, **kwargs) -> dict:
    captured.update(kwargs)
    return {"features": []}

  monkeypatch.setattr(
    swissgeo_provider.OpenSearchCatalogueProvider,
    "query",
    fake_query,
  )
  return captured


class TestDefaultSort:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_defaults_to_localised_title_then_id(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang="de", fmt=None)

    _make_provider().query(language="de")

    assert captured["sortby"] == [
      {"property": "title.de", "order": "+"},
      {"property": "id", "order": "+"},
    ]

  @pytest.mark.parametrize("code", ["de", "fr", "it", "en"])
  def test_sort_language_follows_requested_language(self, monkeypatch, code) -> None:
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang=code, fmt=None)

    _make_provider().query(language=code)

    assert captured["sortby"][0]["property"] == f"title.{code}"

  def test_falls_back_to_thread_local_lang(self, monkeypatch) -> None:
    """No ``language`` kwarg: the lang app.py stashed is used instead."""
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang="it", fmt=None)

    _make_provider().query()

    assert captured["sortby"][0]["property"] == "title.it"

  def test_defaults_to_en_without_any_language(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)

    _make_provider().query()

    assert captured["sortby"][0]["property"] == "title.en"

  def test_explicit_sortby_takes_precedence(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang="de", fmt=None)
    explicit = [{"property": "title.fr", "order": "-"}]

    _make_provider().query(sortby=explicit, language="de")

    assert captured["sortby"] == explicit

  def test_tiebreaker_not_appended_to_explicit_sortby(self, monkeypatch) -> None:
    """An explicit sort is passed through untouched, not augmented."""
    captured = _capture_sortby(monkeypatch)
    explicit = [{"property": "id", "order": "-"}]

    _make_provider().query(sortby=explicit)

    assert captured["sortby"] == explicit


class TestDefaultSortWithFreeTextSearch:
  """A ``q`` search must stay relevance-ranked.

  Any explicit sort clause makes OpenSearch drop ``_score``, so defaulting to
  the title here would return search hits alphabetically and bury the best
  match.
  """

  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_relevance_is_primary_key_for_search(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang="de", fmt=None)

    _make_provider().query(q="zermatt", language="de")

    assert captured["sortby"] == [
      {"property": "_score", "order": "-"},
      {"property": "id", "order": "+"},
    ]

  def test_search_still_gets_a_stable_tiebreaker(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)

    _make_provider().query(q="zermatt")

    assert captured["sortby"][-1] == {"property": "id", "order": "+"}

  def test_title_sort_used_when_q_is_empty_string(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)
    set_request_params(lang="de", fmt=None)

    _make_provider().query(q="", language="de")

    assert captured["sortby"][0]["property"] == "title.de"

  def test_explicit_sortby_still_wins_over_relevance(self, monkeypatch) -> None:
    captured = _capture_sortby(monkeypatch)
    explicit = [{"property": "title.de", "order": "+"}]

    _make_provider().query(q="zermatt", sortby=explicit, language="de")

    assert captured["sortby"] == explicit


# ---------------------------------------------------------------------------
# get_fields / mask_prop
#
# These make pygeoapi resolve the nested per-language title leaves: without
# them neither the sortby validation nor the sort-clause builder can see them.
# ---------------------------------------------------------------------------


class TestGetFields:
  def _provider_with_parent_fields(self, monkeypatch, parent_fields=None) -> SwissGeoProvider:
    provider = _make_provider()
    fields = parent_fields if parent_fields is not None else {"q": {"type": "string"}}
    monkeypatch.setattr(
      swissgeo_provider.OpenSearchCatalogueProvider,
      "get_fields",
      lambda _self: fields,
    )
    return provider

  @pytest.mark.parametrize("code", ["de", "fr", "it", "en"])
  def test_registers_title_leaf_per_language(self, monkeypatch, code) -> None:
    provider = self._provider_with_parent_fields(monkeypatch)
    assert f"title.{code}" in provider.get_fields()

  def test_title_leaves_typed_string_to_reach_raw_subfield(self, monkeypatch) -> None:
    """``string`` is what makes pygeoapi append ``.raw`` to the sort property."""
    provider = self._provider_with_parent_fields(monkeypatch)
    assert provider.get_fields()["title.de"] == {"type": "string"}

  def test_id_registered_as_keyword(self, monkeypatch) -> None:
    """``keyword`` keeps pygeoapi from appending ``.raw`` to ``id``."""
    provider = self._provider_with_parent_fields(monkeypatch)
    assert provider.get_fields()["id"] == {"type": "keyword"}

  def test_score_registered_as_keyword(self, monkeypatch) -> None:
    provider = self._provider_with_parent_fields(monkeypatch)
    assert provider.get_fields()["_score"] == {"type": "keyword"}

  def test_parent_fields_preserved(self, monkeypatch) -> None:
    provider = self._provider_with_parent_fields(
      monkeypatch, {"q": {"type": "string"}, "keywords": {"type": "keyword"}}
    )
    fields = provider.get_fields()
    assert fields["q"] == {"type": "string"}
    assert fields["keywords"] == {"type": "keyword"}

  def test_written_back_to_fields_attribute(self, monkeypatch) -> None:
    """``BaseProvider.fields`` reads ``_fields`` directly, not get_fields()."""
    provider = self._provider_with_parent_fields(monkeypatch)
    provider.get_fields()
    assert "title.de" in provider.fields
    assert "id" in provider.fields


class TestMaskProp:
  def test_root_level_id_not_prefixed(self) -> None:
    assert _make_provider().mask_prop("id") == "id"

  def test_score_not_prefixed(self) -> None:
    assert _make_provider().mask_prop("_score") == "_score"

  def test_nested_property_still_prefixed(self) -> None:
    assert _make_provider().mask_prop("title.de") == "properties.title.de"

  def test_other_properties_unaffected(self) -> None:
    assert _make_provider().mask_prop("keywords") == "properties.keywords"


# ---------------------------------------------------------------------------
# End-to-end sort clause
#
# Exercises pygeoapi's own sortby translation against a stubbed OpenSearch
# client, to pin down the exact field paths the backend receives. This is the
# part that breaks silently: OpenSearch refuses to sort on a `text` field, so
# the clause must target the `raw` keyword sub-field from the index mapping.
# ---------------------------------------------------------------------------


def _provider_with_fake_client() -> tuple[SwissGeoProvider, list]:
  """Provider wired to a fake OpenSearch client that records query bodies."""
  provider = _make_provider()
  bodies = []

  class FakeClient:
    def search(self, index, from_, size, body) -> dict:  # noqa: ARG002
      bodies.append(body)
      return {"hits": {"total": {"value": 0}, "hits": []}}

  provider.os_ = FakeClient()
  provider.index_name = "swissgeo-catalog"
  provider.properties = []
  provider.select_properties = []
  provider.time_field = None
  provider.id_field = "externalId"
  provider._fields = {
    "title.de": {"type": "string"},
    "title.fr": {"type": "string"},
    "id": {"type": "keyword"},
    "_score": {"type": "keyword"},
  }
  return provider, bodies


class TestSortClauseSentToOpenSearch:
  def setup_method(self) -> None:
    _local.__dict__.clear()

  def test_title_sort_targets_raw_keyword_subfield(self) -> None:
    """`properties.title.de` is a text field; sorting it directly would fail."""
    provider, bodies = _provider_with_fake_client()
    set_request_params(lang="de", fmt=None)

    provider.query(language="de")

    assert bodies[0]["sort"][0] == {"properties.title.de.raw": {"order": "asc"}}

  def test_tiebreaker_sorts_on_root_id_without_raw(self) -> None:
    provider, bodies = _provider_with_fake_client()
    set_request_params(lang="de", fmt=None)

    provider.query(language="de")

    assert bodies[0]["sort"][1] == {"id": {"order": "asc"}}

  def test_descending_explicit_sort_translated(self) -> None:
    provider, bodies = _provider_with_fake_client()

    provider.query(sortby=[{"property": "title.fr", "order": "-"}])

    assert bodies[0]["sort"] == [{"properties.title.fr.raw": {"order": "desc"}}]

  def test_search_sorts_on_score_metadata_field(self) -> None:
    """``_score`` must reach OpenSearch bare: neither prefixed nor ``.raw``-ed."""
    provider, bodies = _provider_with_fake_client()

    provider.query(q="zermatt")

    assert bodies[0]["sort"] == [
      {"_score": {"order": "desc"}},
      {"id": {"order": "asc"}},
    ]
