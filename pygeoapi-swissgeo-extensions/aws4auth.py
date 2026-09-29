"""AWS SigV4 auth for the OpenSearch client."""

import logging
import os
import time
from functools import cache

import boto3
from botocore.credentials import Credentials
from opensearchpy import AWSV4SignerAuth

LOGGER = logging.getLogger(__name__)

_CRED_RETRIES = 3
_CRED_RETRY_DELAY = 2.0


def wait_for_credentials(session: boto3.Session) -> Credentials:
  # IMDS credential fetches can fail transiently on startup due to network errors
  for attempt in range(1, _CRED_RETRIES + 1):
    creds = session.get_credentials()
    if creds is not None and creds.get_frozen_credentials().access_key:
      return creds
    if attempt < _CRED_RETRIES:
      LOGGER.warning(
        "AWS credentials not ready (attempt %d/%d), retrying in %.1fs",
        attempt,
        _CRED_RETRIES,
        _CRED_RETRY_DELAY,
      )
      time.sleep(_CRED_RETRY_DELAY)
  raise RuntimeError(f"AWS credentials unavailable after {_CRED_RETRIES} attempts")


@cache
def _get_auth(region: str, service: str) -> AWSV4SignerAuth:
  # A boto3 Session is expensive to build, so there is only one per process.
  # The signer keeps the refreshable credentials and freezes them per request,
  # so temporary credentials are renewed before they expire.
  credentials = wait_for_credentials(boto3.Session())
  LOGGER.info("Configuring AWS SigV4 auth (region=%s service=%s)", region, service)
  return AWSV4SignerAuth(credentials, region, service)


def aws_auth(provider_def: dict) -> AWSV4SignerAuth:
  """Return the process-wide SigV4 signer for the provider's region and service."""
  region = provider_def.get(
    "aws_region",
    os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
  )
  service = provider_def.get("aws_service", "es")
  return _get_auth(region, service)
