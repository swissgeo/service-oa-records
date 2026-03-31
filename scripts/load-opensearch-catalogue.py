#!/usr/bin/env python3
"""
Index three OpenSearch collections:
  1. swissgeo-catalog       — from per-language item files in ITEMS_DIR
  2. swissgeo-distributions — from per-language collection files in COLLECTIONS_DIR
  3. geoadmin-services      — from per-language item files in SERVICES_ITEMS_DIR

Usage:
    python3 load-opensearch-catalogue.py
    OS_URL=http://localhost:9200 python3 load-opensearch-catalogue.py
    FORCE=1 python3 load-opensearch-catalogue.py   # delete and recreate indices
    COLLECTIONS_DIR=aws-s3-records/oar/v0/collections python3 load-opensearch-catalogue.py

Requires: opensearch-py
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from opensearchpy import OpenSearch, helpers

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
CATALOG_INDEX = os.environ.get("OS_CATALOG_INDEX", "swissgeo-catalog")
DISTRIBUTIONS_INDEX = os.environ.get("OS_DISTRIBUTIONS_INDEX", "swissgeo-distributions")
SERVICES_INDEX = os.environ.get("OS_SERVICES_INDEX", "geoadmin-services")
ITEMS_DIR = Path(
    os.environ.get("ITEMS_DIR", "v0/oar/v0/collections/swissgeo.catalog/items")
)
COLLECTIONS_DIR = Path(os.environ.get("COLLECTIONS_DIR", "v0/oar/v0/collections"))
SERVICES_ITEMS_DIR = Path(
    os.environ.get(
        "SERVICES_ITEMS_DIR", "v0/oar/v0/collections/geoadmin.services/items"
    )
)
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")

LANGUAGES = ["de", "en", "fr", "it"]

CATALOG_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "type": {"type": "keyword"},
            "geometry": {"type": "geo_shape"},
            "properties": {
                "properties": {
                    "type": {"type": "keyword"},
                    "title_de": {"type": "text"},
                    "title_en": {"type": "text"},
                    "title_fr": {"type": "text"},
                    "title_it": {"type": "text"},
                    "description_de": {"type": "text"},
                    "description_en": {"type": "text"},
                    "description_fr": {"type": "text"},
                    "description_it": {"type": "text"},
                    "keywords": {"type": "keyword"},
                    "preferredDistributionId": {"type": "keyword"},
                }
            },
        }
    }
}

DISTRIBUTIONS_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "type": {"type": "keyword"},
            "properties": {
                "properties": {
                    "title": {"type": "text"},
                }
            },
            "records": {"type": "object", "enabled": False},
        }
    }
}

SERVICES_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "type": {"type": "keyword"},
            "links": {"type": "object", "enabled": False},
            "linkTemplates": {"type": "object", "enabled": False},
            "properties": {
                "properties": {
                    "title": {"type": "text"},
                    "type": {"type": "keyword"},
                }
            },
        }
    }
}

JSONLD_CONTEXT = {
    "wms": "http://www.opengis.net/wms",
    "wmts": "http://www.opengis.net/wmts/1.0",
    "ows": "http://www.opengis.net/ows/1.1",
}

OGC_SCHEMA = "https://schemas.opengis.net/ogcapi/records/part1/1.0/openapi/schemas/recordGeoJSON.yaml"


WMS_SERVICE_ID = "wms-geoadminch"
WMTS_SERVICE_ID = "wmts-geoadminch"

WMS_LINK_TEMPLATE = {
    "uriTemplate": "https://wms.geo.admin.ch/?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0&FORMAT=text/xml&lang={lang}",
    "rel": "about",
    "title": "WMS Capabilities File",
    "type": "application/xml",
    "variables": {
        "lang": {
            "enum": ["de", "fr", "en", "it"],
            "type": "string",
            "default": "de",
            "description": "Language",
        }
    },
}

WMTS_LINK_TEMPLATE = {
    "uriTemplate": "https://wmts.geo.admin.ch/EPSG/{EPSG}/1.0.0/WMTSCapabilities.xml",
    "rel": "about",
    "title": "WMTS Capabilities File",
    "type": "application/vnd.ogc.wmts_xml",
    "variables": {
        "EPSG": {
            "enum": [2056, 21781, 4326],
            "type": "number",
            "format": "integer",
            "default": 2056,
            "description": "EPSG",
        }
    },
}


def load_catalog_records(items_dir: Path) -> list[dict]:
    """Merge per-language item files into OGC Records-shaped documents."""

    # Group files by record ID
    by_id: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in items_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == 2 and parts[1] in LANGUAGES:
            record_id, lang = parts
            by_id[record_id][lang] = path

    records = []
    for record_id, lang_files in sorted(by_id.items()):
        lang_data: dict[str, dict] = {}
        for lang, path in lang_files.items():
            lang_data[lang] = json.loads(path.read_text())

        # Use the first available language as the base for structure
        base = (
            lang_data.get("de") or lang_data.get("en") or next(iter(lang_data.values()))
        )

        links = [
            link
            for link in base.get("links", [])
            if "services.dev.sgdi.tech" not in link.get("href", "")
        ]
        links.append(
            {
                "href": f"/collections/swissgeo-distributions-os/items/{record_id}",
                "rel": "distributions",
                "title": "Distributions",
            }
        )

        record = {
            "$schema": OGC_SCHEMA,
            "id": record_id,
            "type": base["type"],
            "geometry": base["geometry"],
            "links": links,
            "properties": {
                **{
                    k: v
                    for k, v in base["properties"].items()
                    if k not in ("title", "description", "language")
                },
            },
        }
        for lang in LANGUAGES:
            if lang in lang_data:
                props = lang_data[lang]["properties"]
                record["properties"][f"title_{lang}"] = props.get("title", "")
                record["properties"][f"description_{lang}"] = props.get(
                    "description", ""
                )

        records.append(record)

    return records


def load_services_records(services_items_dir: Path) -> list[dict]:
    """Load geoadmin service records, merging per-language files."""
    # Group files by service ID (only files with a language extension)
    by_id: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in services_items_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == 2 and parts[1] in LANGUAGES:
            service_id, lang = parts
            by_id[service_id][lang] = path

    records = []
    for service_id, lang_files in sorted(by_id.items()):
        lang_data: dict[str, dict] = {}
        for lang, path in lang_files.items():
            lang_data[lang] = json.loads(path.read_text())

        base = (
            lang_data.get("de") or lang_data.get("en") or next(iter(lang_data.values()))
        )

        record = {
            "id": service_id,
            "type": base.get("type", "Feature"),
            "links": base.get("links", []),
            "properties": {
                **{
                    k: v
                    for k, v in base.get("properties", {}).items()
                    if k not in ("title",)
                },
            },
        }
        if "linkTemplates" in base:
            record["linkTemplates"] = base["linkTemplates"]

        for lang in LANGUAGES:
            if lang in lang_data:
                props = lang_data[lang].get("properties", {})
                record["properties"][f"title_{lang}"] = props.get("title", "")

        records.append(record)

    return records


def load_distribution_records(collections_dir: Path) -> list[dict]:
    """Load distribution records from per-language collection files."""
    # Pick one file per dataset ID (prefer .de)
    by_id: dict[str, Path] = {}
    for path in collections_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == 2 and parts[1] in LANGUAGES:
            dataset_id, lang = parts
            if dataset_id not in by_id or lang == "de":
                by_id[dataset_id] = path

    records = []
    for dataset_id, path in sorted(by_id.items()):
        data = json.loads(path.read_text())
        title = data.pop("title", None)
        data.setdefault("properties", {})
        if title is not None:
            data["properties"]["title"] = title.removeprefix("Distributions for ")

        catalog_href = f"/collections/swissgeo-catalogue-os/items/{dataset_id}"
        for dist in data.get("records", []):
            new_links = []
            for link in dist.get("links", []):
                if link.get("title") == "Dataset Record":
                    new_links.append(
                        {
                            "href": catalog_href,
                            "rel": "dataset",
                            "title": "Dataset Record",
                        }
                    )
                elif link.get("rel") == "service":
                    service_id = link["href"].rsplit("/", 1)[-1]
                    new_links.append(
                        {
                            "href": f"/collections/geoadmin-services-os/items/{service_id}",
                            "rel": "service",
                        }
                    )
                else:
                    new_links.append(link)
            dist["links"] = new_links

        records.append(data)

    return records


def ensure_index(client: OpenSearch, index: str, mapping: dict) -> bool:
    """Create index if it doesn't exist. Returns True if indexing should proceed."""
    if client.indices.exists(index=index):
        if FORCE:
            print(f"  FORCE=1 — deleting index '{index}' ...")
            client.indices.delete(index=index)
        else:
            count = client.count(index=index)["count"]
            print(f"  Index '{index}' already exists with {count} documents.")
            print("  Set FORCE=1 to delete and recreate. Skipping.")
            return False

    print(f"  Creating index '{index}' ...", end=" ", flush=True)
    client.indices.create(index=index, body=mapping)
    print("done")
    return True


def index_documents(
    client: OpenSearch, index: str, records: list[dict], id_field: str = "id"
):
    def _actions():
        for rec in records:
            yield {
                "_index": index,
                "_id": rec[id_field],
                "_source": rec,
            }

    print(f"  Indexing {len(records)} documents ...", end=" ", flush=True)
    ok, errors = helpers.bulk(client, _actions(), raise_on_error=False)
    if errors:
        print(f"\n  WARNING: {len(errors)} errors during indexing:")
        for e in errors[:5]:
            print(f"    {e}")
    print(f"{ok} indexed")


def main():
    print("=" * 60)
    print(" Indexing Swiss Geodata Catalogue into OpenSearch")
    print("=" * 60)
    print(f"Items dir:         {ITEMS_DIR}")
    print(f"Collections dir:   {COLLECTIONS_DIR}")
    print(f"Services items dir:{SERVICES_ITEMS_DIR}")
    print(f"Target:            {OS_URL}")
    print()

    if not ITEMS_DIR.exists():
        print(f"ERROR: Items directory not found: {ITEMS_DIR}")
        sys.exit(1)
    if not COLLECTIONS_DIR.exists():
        print(f"ERROR: Collections directory not found: {COLLECTIONS_DIR}")
        sys.exit(1)
    if not SERVICES_ITEMS_DIR.exists():
        print(f"ERROR: Services items directory not found: {SERVICES_ITEMS_DIR}")
        sys.exit(1)

    client = OpenSearch(OS_URL, verify_certs=False)
    if not client.ping():
        print(f"ERROR: Cannot connect to OpenSearch at {OS_URL}")
        sys.exit(1)

    # --- swissgeo-catalog ---
    print(f"[{CATALOG_INDEX}]")
    if ensure_index(client, CATALOG_INDEX, CATALOG_INDEX_MAPPING):
        print(f"  Loading records from {ITEMS_DIR} ...", end=" ", flush=True)
        catalog_records = load_catalog_records(ITEMS_DIR)
        print(f"{len(catalog_records)} records")
        index_documents(client, CATALOG_INDEX, catalog_records)
    print()

    # --- swissgeo-distributions ---
    print(f"[{DISTRIBUTIONS_INDEX}]")
    if ensure_index(client, DISTRIBUTIONS_INDEX, DISTRIBUTIONS_INDEX_MAPPING):
        print(
            f"  Loading distributions from {COLLECTIONS_DIR} ...", end=" ", flush=True
        )
        dist_records = load_distribution_records(COLLECTIONS_DIR)
        print(f"{len(dist_records)} records")
        index_documents(client, DISTRIBUTIONS_INDEX, dist_records)
    print()

    # --- geoadmin-services ---
    print(f"[{SERVICES_INDEX}]")
    if ensure_index(client, SERVICES_INDEX, SERVICES_INDEX_MAPPING):
        print(f"  Loading services from {SERVICES_ITEMS_DIR} ...", end=" ", flush=True)
        service_records = load_services_records(SERVICES_ITEMS_DIR)
        print(f"{len(service_records)} records")
        index_documents(client, SERVICES_INDEX, service_records)
    print()

    print("Done!")


if __name__ == "__main__":
    main()
