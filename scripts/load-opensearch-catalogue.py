#!/usr/bin/env python3
"""Index three OpenSearch collections.

1. swissgeo-catalog       — from per-language item files in ITEMS_DIR
2. swissgeo-distributions — from per-language collection files in COLLECTIONS_DIR
3. geoadmin-services      — from per-language item files in SERVICES_ITEMS_DIR

Steps (run in sequence when no argument is given):
  generate  — build documents and write to .generated/<index>/<id>.json
  index     — create or recreate OpenSearch indices
  import    — bulk-index documents from .generated/<index>/

Usage:
    python3 load-opensearch-catalogue.py [generate|import|index]
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

_TRUTHY = frozenset(("1", "true", "yes"))


def _env_bool(name: str) -> bool:
  """Return True if the env var is set to a truthy value (1, true, yes)."""
  return os.environ.get(name, "").lower() in _TRUTHY


OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_AWS4AUTH = _env_bool("OPENSEARCH_AWS4AUTH")
CATALOG_INDEX = os.environ.get("OS_CATALOG_INDEX", "swissgeo-catalog")
DISTRIBUTIONS_INDEX = os.environ.get(
  "OS_DISTRIBUTIONS_INDEX",
  "swissgeo-distributions",
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
GENERATED_DIR = Path(os.environ.get("GENERATED_DIR", ".generated"))
FORCE = _env_bool("FORCE")

LANGUAGES = ["de", "en", "fr", "it"]

LANG_PARTS = 2

_MAPPINGS_DIR = Path(__file__).parent

OGC_SCHEMA = "https://schemas.opengis.net/ogcapi/records/part1/1.0/openapi/schemas/recordGeoJSON.yaml"


# ---------------------------------------------------------------------------
# Step 1 — generate: build documents and write to generated/<index>/<id>.json
# ---------------------------------------------------------------------------


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

    base = lang_data.get("de") or lang_data.get("en") or next(iter(lang_data.values()))

    links = [link for link in base.get("links", []) if "services.dev.sgdi.tech" not in link.get("href", "")]
    links.append(
      {
        "href": f"/collections/swissgeo-distributions/items/{record_id}",
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
        **{k: v for k, v in base["properties"].items() if k not in ("title", "description", "language")},
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

    base = lang_data.get("de") or lang_data.get("en") or next(iter(lang_data.values()))

    record = {
      "id": service_id,
      "type": base.get("type", "Feature"),
      "links": base.get("links", []),
      "properties": {
        **{k: v for k, v in base.get("properties", {}).items() if k != "title"},
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

    catalog_href = f"/collections/swissgeo-catalogue/items/{dataset_id}"
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
              "href": (f"/collections/geoadmin-services/items/{service_id}"),
              "rel": "service",
            },
          )
        else:
          new_links.append(link)
      dist["links"] = new_links

    records.append(data)

  return records


def step_generate() -> None:
  """Build all documents and write them to generated/<index>/<id>.json."""
  if not ITEMS_DIR.exists():
    log.error("Items directory not found: %s", ITEMS_DIR)
    sys.exit(1)
  if not COLLECTIONS_DIR.exists():
    log.error("Collections directory not found: %s", COLLECTIONS_DIR)
    sys.exit(1)
  if not SERVICES_ITEMS_DIR.exists():
    log.error("Services items directory not found: %s", SERVICES_ITEMS_DIR)
    sys.exit(1)

  index_records = [
    (CATALOG_INDEX, load_catalog_records(ITEMS_DIR)),
    (DISTRIBUTIONS_INDEX, load_distribution_records(COLLECTIONS_DIR)),
    (SERVICES_INDEX, load_services_records(SERVICES_ITEMS_DIR)),
  ]

  for index, records in index_records:
    out_dir = GENERATED_DIR / index
    out_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
      (out_dir / f"{rec['id']}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2),
      )
    log.info("Generated %d documents into %s/", len(records), out_dir)


# ---------------------------------------------------------------------------
# Step 2 — import: bulk-index documents from generated/<index>/
# ---------------------------------------------------------------------------


def step_import(client: OpenSearch) -> None:
  """Bulk-index all documents from generated/<index>/ into OpenSearch."""
  for index in (CATALOG_INDEX, DISTRIBUTIONS_INDEX, SERVICES_INDEX):
    index_dir = GENERATED_DIR / index
    if not index_dir.exists():
      log.warning("Generated dir not found, skipping: %s", index_dir)
      continue

    records = [json.loads(p.read_text()) for p in sorted(index_dir.glob("*.json"))]

    def _actions(idx: str, recs: list[dict]):  # noqa: ANN202
      for rec in recs:
        yield {"_index": idx, "_id": rec["id"], "_source": rec}

    log.info("Indexing %d documents into '%s'", len(records), index)
    ok, errors = helpers.bulk(
      client,
      _actions(index, records),
      raise_on_error=False,
    )
    if errors:
      log.warning("%d errors during indexing:", len(errors))
      for e in errors[:5]:
        log.warning("  %s", e)
    log.info("%d documents indexed into '%s'", ok, index)


# ---------------------------------------------------------------------------
# Step 3 — index: create or recreate OpenSearch indices
# ---------------------------------------------------------------------------


def _load_mapping(index_name: str) -> dict:
  """Load an OpenSearch index mapping from its sidecar JSON file."""
  path = _MAPPINGS_DIR / f"opensearch-index-mapping-{index_name}.json"
  return json.loads(path.read_text())


def step_index(client: OpenSearch) -> None:
  """Create indices that don't exist yet; delete and recreate if FORCE=1."""
  for index in (CATALOG_INDEX, DISTRIBUTIONS_INDEX, SERVICES_INDEX):
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
        continue

    log.info("Creating index '%s'", index)
    client.indices.create(index=index, body=_load_mapping(index))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_STEPS = ("generate", "index", "import")


def _make_client() -> OpenSearch:
  """Build and return an OpenSearch client (with optional AWS4Auth)."""
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
    return OpenSearch(
      OPENSEARCH_URL,
      http_auth=auth,
      use_ssl=OPENSEARCH_URL.startswith("https"),
      verify_certs=True,
      connection_class=RequestsHttpConnection,
    )
  return OpenSearch(OPENSEARCH_URL, verify_certs=False)


def main() -> None:
  """Run generate → index → import (or a single named step)."""
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
  )

  args = sys.argv[1:]
  if args and args[0] not in _STEPS:
    log.error("Usage: %s [%s]", sys.argv[0], "|".join(_STEPS))
    sys.exit(1)

  steps = (args[0],) if args else _STEPS

  if "generate" in steps:
    log.info("=== Step 1: generate ===")
    step_generate()

  if "import" in steps or "index" in steps:
    client = _make_client()
    if not client.ping():
      log.error("Cannot connect to OpenSearch at %s", OPENSEARCH_URL)
      sys.exit(1)

    if "index" in steps:
      log.info("=== Step 2: index ===")
      step_index(client)

    if "import" in steps:
      log.info("=== Step 3: import ===")
      step_import(client)

  log.info("Done!")


if __name__ == "__main__":
  main()
