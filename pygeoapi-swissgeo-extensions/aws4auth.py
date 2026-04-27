"""AWS SigV4 monkey-patch for pygeoapi's OpenSearch provider."""

import logging
import os
import time
from contextlib import contextmanager

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

LOGGER = logging.getLogger(__name__)

_CRED_RETRIES = 3
_CRED_RETRY_DELAY = 2.0


def wait_for_credentials() -> None:
  # IMDS credential fetches can fail transiently on startup due to network errors
  for attempt in range(1, _CRED_RETRIES + 1):
    creds = boto3.Session().get_credentials()
    if creds is not None and creds.get_frozen_credentials().access_key:
      return
    if attempt == _CRED_RETRIES:
      raise RuntimeError(f"AWS credentials unavailable after {_CRED_RETRIES} attempts")
    LOGGER.warning(
      "AWS credentials not ready (attempt %d/%d), retrying in %.1fs",
      attempt,
      _CRED_RETRIES,
      _CRED_RETRY_DELAY,
    )
    time.sleep(_CRED_RETRY_DELAY)


@contextmanager
def patched_opensearch(provider_def: dict):
  """Context manager that monkey-patches OpenSearch with AWS SigV4 auth, then restores it.

  Usage::

      with patched_opensearch(provider_def):
          super().__init__(provider_def)
  """
  wait_for_credentials()

  import pygeoapi.provider.opensearch_ as _os_mod  # noqa: PLC0415

  region = provider_def.get(
    "aws_region",
    os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
  )
  service = provider_def.get("aws_service", "es")
  LOGGER.info(
    "Configuring AWS SigV4 auth (region=%s service=%s)",
    region,
    service,
  )
  credentials = boto3.Session().get_credentials().get_frozen_credentials()
  awsauth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    region,
    service,
    session_token=credentials.token,
  )

  _original_opensearch = _os_mod.OpenSearch

  def _aws_opensearch(host, **kwargs):  # noqa: ANN001, ANN202, ARG001
    return OpenSearch(
      hosts=[host],
      http_auth=awsauth,
      use_ssl=True,
      verify_certs=True,
      connection_class=RequestsHttpConnection,
    )

  _os_mod.OpenSearch = _aws_opensearch  # ty: ignore[invalid-assignment]
  try:
    yield
  finally:
    _os_mod.OpenSearch = _original_opensearch
