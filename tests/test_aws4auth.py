"""Tests for the AWS SigV4 auth of the OpenSearch client."""

import sys
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "pygeoapi-swissgeo-extensions"))

import aws4auth
from botocore.credentials import Credentials

if TYPE_CHECKING:
  import boto3


class _FakeSession:
  instances = 0

  def __init__(self, credentials=None) -> None:
    _FakeSession.instances += 1
    self.credentials = credentials

  def get_credentials(self) -> Credentials | None:
    return self.credentials


@pytest.fixture(autouse=True)
def _fake_boto3(monkeypatch) -> Generator[None, None, None]:
  aws4auth._get_auth.cache_clear()
  _FakeSession.instances = 0
  monkeypatch.setattr(aws4auth.boto3, "Session", lambda: _FakeSession(Credentials("key", "secret")))
  monkeypatch.setattr(aws4auth.time, "sleep", lambda _seconds: None)
  yield
  aws4auth._get_auth.cache_clear()


def test_signer_is_shared_across_calls() -> None:
  first = aws4auth.aws_auth({})
  second = aws4auth.aws_auth({})

  assert first is second
  assert _FakeSession.instances == 1


def test_region_and_service_from_provider_def() -> None:
  auth = aws4auth.aws_auth({"aws_region": "eu-west-1", "aws_service": "aoss"})

  assert auth.signer.region == "eu-west-1"
  assert auth.service == "aoss"


def test_region_defaults_to_env(monkeypatch) -> None:
  monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

  auth = aws4auth.aws_auth({})

  assert auth.signer.region == "us-east-1"
  assert auth.service == "es"


def test_wait_for_credentials_retries_until_available() -> None:
  credentials = Credentials("key", "secret")
  responses = iter([None, credentials])

  class _Session:
    def get_credentials(self) -> Credentials | None:
      return next(responses)

  assert aws4auth.wait_for_credentials(cast("boto3.Session", _Session())) is credentials


def test_wait_for_credentials_gives_up() -> None:
  with pytest.raises(RuntimeError, match="unavailable"):
    aws4auth.wait_for_credentials(cast("boto3.Session", _FakeSession()))
