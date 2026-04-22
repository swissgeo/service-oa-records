"""Tests for swissgeo_provider helper functions."""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "pygeoapi-swissgeo-extensions"))

from swissgeo_provider import (
    _apply_lang,
    _ensure_self_link,
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
# _apply_lang
# ---------------------------------------------------------------------------


class TestApplyLang:
    def test_title_replaced_with_localised_variant(self) -> None:
        props = {"title": "Default", "title_de": "Deutsch"}
        _apply_lang(props, "de")
        assert props["title"] == "Deutsch"

    def test_description_replaced_with_localised_variant(self) -> None:
        props = {"description": "Default", "description_fr": "Français"}
        _apply_lang(props, "fr")
        assert props["description"] == "Français"

    def test_falls_back_to_original_when_localised_empty(self) -> None:
        props = {"title": "Default", "title_de": ""}
        _apply_lang(props, "de")
        assert props["title"] == "Default"

    def test_falls_back_to_original_when_localised_missing(self) -> None:
        props = {"title": "Default"}
        _apply_lang(props, "it")
        assert props["title"] == "Default"

    def test_per_lang_fields_stripped(self) -> None:
        props = {
            "title": "T",
            "title_de": "T-de",
            "title_fr": "T-fr",
            "description_en": "D-en",
        }
        _apply_lang(props, "de")
        assert "title_de" not in props
        assert "title_fr" not in props
        assert "description_en" not in props

    def test_non_lang_fields_untouched(self) -> None:
        props = {"title": "T", "extra": "keep me"}
        _apply_lang(props, "en")
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

    def test_prepends_server_url_when_available(self) -> None:
        set_request_params(lang=None, fmt=None, server_url="https://api.example.com")
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

    def test_patches_same_host_link(self) -> None:
        set_request_params(lang=None, fmt=None, server_url="https://api.example.com")
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

    def test_prepends_server_url_to_relative_link(self) -> None:
        set_request_params(lang=None, fmt=None, server_url="https://api.example.com")
        links = [{"href": "/collections/col"}]
        _patch_links(links, "en", None)
        assert links[0]["href"].startswith("https://api.example.com/collections/col")
