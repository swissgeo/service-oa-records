#!/usr/bin/env python3
"""
Index all JSON files from the sample-records/ directory into OpenSearch.

Usage:
    python3 load-sample-records.py
    OS_URL=http://localhost:9200 RECORDS_DIR=sample-records python3 load-sample-records.py
    FORCE=1 python3 load-sample-records.py   # delete and recreate index

Requires: opensearch-py
"""

import json
import os
import sys
from pathlib import Path

from opensearchpy import OpenSearch, helpers

OS_URL = os.environ.get("OS_URL", "http://localhost:9200")
INDEX = os.environ.get("OS_INDEX", "sample-records")
RECORDS_DIR = Path(os.environ.get("RECORDS_DIR", "sample-records"))
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")

INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "type": {"type": "keyword"},
            "geometry": {"type": "geo_shape"},
            "properties": {
                "properties": {
                    "type": {"type": "keyword"},
                    "title_de": {
                        "type": "text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "title_en": {
                        "type": "text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "title_fr": {
                        "type": "text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "title_it": {
                        "type": "text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "description_de": {"type": "text"},
                    "description_en": {"type": "text"},
                    "description_fr": {"type": "text"},
                    "description_it": {"type": "text"},
                }
            },
        }
    }
}


def load_records(records_dir: Path) -> list[dict]:
    records = []
    for path in sorted(records_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        # Derive _metadata-anytext for full-text search
        props = rec.get("properties", {})
        anytext_parts = [
            props.get(f"{field}_{lang}", "")
            for field in ("title", "description")
            for lang in ("de", "en", "fr", "it")
        ]
        props["_metadata-anytext"] = " ".join(filter(None, anytext_parts))
        records.append(rec)
    return records


def main():
    print("=" * 60)
    print(" Indexing sample records into OpenSearch")
    print("=" * 60)
    print(f"Source:  {RECORDS_DIR}")
    print(f"Target:  {OS_URL}/{INDEX}")
    print()

    if not RECORDS_DIR.exists():
        print(f"ERROR: Records directory not found: {RECORDS_DIR}")
        sys.exit(1)

    client = OpenSearch(OS_URL, verify_certs=False)
    if not client.ping():
        print(f"ERROR: Cannot connect to OpenSearch at {OS_URL}")
        sys.exit(1)

    if client.indices.exists(index=INDEX):
        if FORCE:
            print(f"  FORCE=1 — deleting index '{INDEX}' ...")
            client.indices.delete(index=INDEX)
        else:
            count = client.count(index=INDEX)["count"]
            print(f"  Index '{INDEX}' already exists with {count} documents.")
            print("  Set FORCE=1 to delete and recreate. Exiting.")
            sys.exit(0)

    print(f"  Creating index '{INDEX}' ...", end=" ", flush=True)
    client.indices.create(index=INDEX, body=INDEX_MAPPING)
    print("done")

    records = load_records(RECORDS_DIR)
    print(f"  Loaded {len(records)} records from {RECORDS_DIR}")

    def _actions():
        for rec in records:
            yield {
                "_index": INDEX,
                "_id": rec["id"],
                "_source": rec,
            }

    print(f"  Indexing {len(records)} documents ...", end=" ", flush=True)
    ok, errors = helpers.bulk(client, _actions(), raise_on_error=False)
    if errors:
        print(f"\n  WARNING: {len(errors)} errors during indexing:")
        for e in errors[:5]:
            print(f"    {e}")
    print(f"{ok} indexed")
    print("\nDone!")


if __name__ == "__main__":
    main()
