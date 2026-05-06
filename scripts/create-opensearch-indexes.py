#!/usr/bin/env python3
"""Create OpenSearch indexes with their mappings if they don't exist yet.

Usage:
    python3 create-opensearch-indexes.py
    OPENSEARCH_URL=http://localhost:9200 python3 create-opensearch-indexes.py
    FORCE=1 python3 create-opensearch-indexes.py   # delete and recreate indexes
    OPENSEARCH_AWS4AUTH=1 python3 create-opensearch-indexes.py

Requires: opensearch-py
"""

import json
import logging
import os
import sys
from pathlib import Path

from opensearchpy import OpenSearch, RequestsHttpConnection

log = logging.getLogger(__name__)

_TRUTHY = frozenset(("1", "true", "yes"))


def _env_bool(name: str) -> bool:
  return os.environ.get(name, "").lower() in _TRUTHY


OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_AWS4AUTH = _env_bool("OPENSEARCH_AWS4AUTH")
CATALOG_INDEX = os.environ.get("OS_CATALOG_INDEX", "swissgeo-catalog")
DISTRIBUTIONS_INDEX = os.environ.get("OS_DISTRIBUTIONS_INDEX", "swissgeo-distributions")
SERVICES_INDEX = os.environ.get("OS_SERVICES_INDEX", "geoadmin-services")
FORCE = _env_bool("FORCE")

_MAPPINGS_DIR = Path(__file__).parent


def _load_mapping(index_name: str) -> dict:
  path = _MAPPINGS_DIR / f"opensearch-index-mapping-{index_name}.json"
  return json.loads(path.read_text())


def _make_client() -> OpenSearch:
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


def create_indexes(client: OpenSearch) -> None:
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
    log.info("Index '%s' created.", index)


def main() -> None:
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

  client = _make_client()
  if not client.ping():
    log.error("Cannot connect to OpenSearch at %s", OPENSEARCH_URL)
    sys.exit(1)

  create_indexes(client)
  log.info("Done!")


if __name__ == "__main__":
  main()
