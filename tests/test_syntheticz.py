"""Tests for the syntheticz endpoint."""

import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from opensearchpy.exceptions import TransportError

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "pygeoapi-swissgeo-extensions"))

import swissgeo_provider
import syntheticz
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
from version import __version__

_MAPPING = {
    "properties": {
        "de": {"properties": {"properties": {"properties": {"title": {"type": "text"}}}}},
    },
}

_RESOURCES = {
    "swissgeo-catalog": {
        "type": "collection",
        "providers": [
            {
                "type": "record",
                "name": "swissgeo_provider.SwissGeoProvider",
                "data": "http://opensearch:9200/swissgeo-catalog",
            },
        ],
    },
    "swissgeo-distributions": {
        "type": "collection",
        "providers": [
            {
                "type": "record",
                "name": "swissgeo_provider.SwissGeoProvider",
                "data": "http://opensearch:9200/swissgeo-distributions",
            },
        ],
    },
    "other": {
        "type": "collection",
        "providers": [{"type": "feature", "name": "GeoJSON", "data": "/tmp/other.json"}],  # noqa: S108
    },
}


@pytest.fixture
def opensearch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock OpenSearch client used by the providers; all indexes exist by default."""
    client = MagicMock()
    client.ping.return_value = True
    client.indices.exists.return_value = True
    monkeypatch.setattr(swissgeo_provider, "_shared_state", {})
    monkeypatch.setattr(swissgeo_provider, "_create_client", lambda *_: client)
    monkeypatch.setattr(
        swissgeo_provider.SwissGeoProvider, "_fetch_mapping", lambda _provider: _MAPPING
    )
    return client


@pytest.fixture
def client(opensearch_client: MagicMock) -> Iterator[TestClient]:  # noqa: ARG001
    app = Starlette(routes=[Route("/syntheticz", syntheticz.syntheticz_endpoint(_RESOURCES))])
    with TestClient(app) as test_client:
        yield test_client


def test_syntheticz_up(client: TestClient, opensearch_client: MagicMock) -> None:
    response = client.get("/syntheticz")

    assert response.status_code == 200, (
        f"Unexpected response {response.status_code}: {response.text}"
    )
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "service": {"name": "service-oa-records", "version": __version__},
        "status": "UP",
        "external_systems": {"opensearch": {"status": "UP"}},
    }
    assert [call.kwargs for call in opensearch_client.indices.exists.call_args_list] == [
        {"index": "swissgeo-catalog"},
        {"index": "swissgeo-distributions"},
    ]


@pytest.mark.parametrize(
    "side_effect",
    [
        [True, False],
        OpenSearchConnectionError("N/A", "Connection refused", Exception()),
        TransportError(403, "security_exception"),
        RuntimeError("AWS credentials unavailable"),
    ],
)
def test_syntheticz_down(
    client: TestClient,
    opensearch_client: MagicMock,
    side_effect: object,
) -> None:
    opensearch_client.indices.exists.side_effect = side_effect

    response = client.get("/syntheticz")

    assert response.status_code == 500, (
        f"Unexpected response {response.status_code}: {response.text}"
    )
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "service": {"name": "service-oa-records", "version": __version__},
        "status": "DOWN",
        "external_systems": {"opensearch": {"status": "DOWN"}},
    }


def test_syntheticz_down_when_client_creation_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_create_client(*_: object) -> None:
        raise RuntimeError("AWS credentials unavailable")

    monkeypatch.setattr(swissgeo_provider, "_create_client", failing_create_client)

    response = client.get("/syntheticz")

    assert response.status_code == 500
    assert response.json()["status"] == "DOWN"


def test_client_is_shared_with_the_provider(
    opensearch_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []

    def counting_create_client(host: str, _provider_def: dict) -> MagicMock:
        created.append(host)
        return opensearch_client

    monkeypatch.setattr(swissgeo_provider, "_create_client", counting_create_client)

    providers = syntheticz._opensearch_providers(_RESOURCES)
    syntheticz._check_opensearch(providers)
    syntheticz._check_opensearch(providers)

    # One client per index, created by the first check only
    assert created == ["http://opensearch:9200"] * 2
    assert set(swissgeo_provider._shared_state) == {
        "http://opensearch:9200/swissgeo-catalog",
        "http://opensearch:9200/swissgeo-distributions",
    }
