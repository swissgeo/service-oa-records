#!/usr/bin/env python3
"""Index three OpenSearch collections.

1. swissgeo-catalog       — from per-language item files in ITEMS_DIR
2. swissgeo-distributions — from per-language collection files in COLLECTIONS_DIR
3. geoadmin-services      — from per-language item files in SERVICES_ITEMS_DIR

Usage:
    python3 load-opensearch-catalogue.py
    OPENSEARCH_URL=http://localhost:9200 python3 load-opensearch-catalogue.py
    FORCE=1 python3 load-opensearch-catalogue.py   # delete and recreate indices
    COLLECTIONS_DIR=aws-s3-records/oar/v0/collections python3 load-opensearch-catalogue.py

Requires: opensearch-py
"""

import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

from opensearchpy import OpenSearch, RequestsHttpConnection, helpers

log = logging.getLogger(__name__)

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_AWS4AUTH = os.environ.get("OPENSEARCH_AWS4AUTH", "").lower() in (
    "1",
    "true",
    "yes",
)
CATALOG_INDEX = os.environ.get("OS_CATALOG_INDEX", "swissgeo-catalog")
DISTRIBUTIONS_INDEX = os.environ.get(
    "OS_DISTRIBUTIONS_INDEX", "swissgeo-distributions",
)
SERVICES_INDEX = os.environ.get("OS_SERVICES_INDEX", "geoadmin-services")
ITEMS_DIR = Path(
    os.environ.get(
        "ITEMS_DIR",
        "static-s3/api/oar/v0/collections/swissgeo.catalog/items",
    ),
)
COLLECTIONS_DIR = Path(
    os.environ.get("COLLECTIONS_DIR", "static-s3/api/oar/v0/collections"),
)
SERVICES_ITEMS_DIR = Path(
    os.environ.get(
        "SERVICES_ITEMS_DIR",
        "static-s3/api/oar/v0/collections/geoadmin.services/items",
    ),
)
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")

LANGUAGES = ["de", "en", "fr", "it"]

LANG_PARTS = 2

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
                },
            },
        },
    },
}

DISTRIBUTIONS_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "type": {"type": "keyword"},
            "properties": {
                "properties": {
                    "title": {"type": "text"},
                },
            },
            "records": {"type": "object", "enabled": False},
        },
    },
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
                },
            },
        },
    },
}

OGC_SCHEMA = (
    "https://schemas.opengis.net/ogcapi/records/part1/1.0/openapi/schemas/"
    "recordGeoJSON.yaml"
)


def load_catalog_records(items_dir: Path) -> list[dict]:
    """Merge per-language item files into OGC Records-shaped documents."""
    by_id: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in items_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == LANG_PARTS and parts[1] in LANGUAGES:
            record_id, lang = parts
            by_id[record_id][lang] = path

    records = []
    for record_id, lang_files in sorted(by_id.items()):
        lang_data: dict[str, dict] = {}
        for lang, path in lang_files.items():
            lang_data[lang] = json.loads(path.read_text())

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
            },
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
                    "description",
                    "",
                )

        records.append(record)

    return records


def load_services_records(services_items_dir: Path) -> list[dict]:
    """Load geoadmin service records, merging per-language files."""
    by_id: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in services_items_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == LANG_PARTS and parts[1] in LANGUAGES:
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
                    if k != "title"
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
    by_id: dict[str, Path] = {}
    for path in collections_dir.iterdir():
        parts = path.name.rsplit(".", 1)
        if len(parts) == LANG_PARTS and parts[1] in LANGUAGES:
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
                        },
                    )
                elif link.get("rel") == "service":
                    service_id = link["href"].rsplit("/", 1)[-1]
                    new_links.append(
                        {
                            "href": (
                                f"/collections/geoadmin-services-os/items/{service_id}"
                            ),
                            "rel": "service",
                        },
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
            log.info("FORCE=1 — deleting index '%s'", index)
            client.indices.delete(index=index)
        else:
            count = client.count(index=index)["count"]
            log.info(
                "Index '%s' already exists with %d documents. Set FORCE=1 to recreate.",
                index,
                count,
            )
            return False

    log.info("Creating index '%s'", index)
    client.indices.create(index=index, body=mapping)
    return True


def index_documents(
    client: OpenSearch,
    index: str,
    records: list[dict],
    id_field: str = "id",
) -> None:
    """Bulk-index records into the given OpenSearch index."""

    def _actions() -> dict:
        for rec in records:
            yield {
                "_index": index,
                "_id": rec[id_field],
                "_source": rec,
            }

    log.info("Indexing %d documents into '%s'", len(records), index)
    ok, errors = helpers.bulk(client, _actions(), raise_on_error=False)
    if errors:
        log.warning("%d errors during indexing:", len(errors))
        for e in errors[:5]:
            log.warning("  %s", e)
    log.info("%d documents indexed into '%s'", ok, index)


def main() -> None:
    """Entry point: validate directories, connect, and index all collections."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log.info("Items dir:          %s", ITEMS_DIR)
    log.info("Collections dir:    %s", COLLECTIONS_DIR)
    log.info("Services items dir: %s", SERVICES_ITEMS_DIR)
    log.info("Target:             %s", OPENSEARCH_URL)

    if not ITEMS_DIR.exists():
        log.error("Items directory not found: %s", ITEMS_DIR)
        sys.exit(1)
    if not COLLECTIONS_DIR.exists():
        log.error("Collections directory not found: %s", COLLECTIONS_DIR)
        sys.exit(1)
    if not SERVICES_ITEMS_DIR.exists():
        log.error("Services items directory not found: %s", SERVICES_ITEMS_DIR)
        sys.exit(1)

    if OPENSEARCH_AWS4AUTH:
        import boto3  # noqa: PLC0415
        from requests_aws4auth import AWS4Auth  # noqa: PLC0415

        region = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            "es",
            session_token=credentials.token,
        )
        client = OpenSearch(
            OPENSEARCH_URL,
            http_auth=auth,
            use_ssl=OPENSEARCH_URL.startswith("https"),
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
    else:
        client = OpenSearch(OPENSEARCH_URL, verify_certs=False)

    if not client.ping():
        log.error("Cannot connect to OpenSearch at %s", OPENSEARCH_URL)
        sys.exit(1)

    if ensure_index(client, CATALOG_INDEX, CATALOG_INDEX_MAPPING):
        catalog_records = load_catalog_records(ITEMS_DIR)
        index_documents(client, CATALOG_INDEX, catalog_records)

    if ensure_index(client, DISTRIBUTIONS_INDEX, DISTRIBUTIONS_INDEX_MAPPING):
        dist_records = load_distribution_records(COLLECTIONS_DIR)
        index_documents(client, DISTRIBUTIONS_INDEX, dist_records)

    if ensure_index(client, SERVICES_INDEX, SERVICES_INDEX_MAPPING):
        service_records = load_services_records(SERVICES_ITEMS_DIR)
        index_documents(client, SERVICES_INDEX, service_records)

    log.info("Done!")


if __name__ == "__main__":
    main()
