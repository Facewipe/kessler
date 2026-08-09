"""Tests for the optional API-key authentication middleware."""

from fastapi.testclient import TestClient

from .conftest import TEST_NORAD_ID


def test_open_when_env_unset(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("KESSLER_API_KEYS", raising=False)

    response = client.get(f"/satellites/{TEST_NORAD_ID}/position")

    assert response.status_code == 200


def test_health_always_open_even_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_API_KEYS", "secret-key")

    response = client.get("/health")

    assert response.status_code == 200


def test_401_without_key_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_API_KEYS", "secret-key")

    response = client.get(f"/satellites/{TEST_NORAD_ID}/position")

    assert response.status_code == 401


def test_401_with_wrong_key(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_API_KEYS", "secret-key")

    response = client.get(
        f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "wrong-key"}
    )

    assert response.status_code == 401


def test_200_with_valid_key(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_API_KEYS", "secret-key")

    response = client.get(
        f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 200


def test_supports_multiple_comma_separated_keys(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_API_KEYS", "key-one, key-two")

    response = client.get(
        f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "key-two"}
    )

    assert response.status_code == 200
