"""Tests for the optional X-API-Key auth middleware."""

from fastapi.testclient import TestClient

from kessler.api import API_KEYS_ENV_VAR, app, get_db

from .conftest import TEST_NORAD_ID


def test_open_when_unset(monkeypatch, db_conn) -> None:
    monkeypatch.delenv(API_KEYS_ENV_VAR, raising=False)
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        with TestClient(app) as client:
            response = client.get(f"/satellites/{TEST_NORAD_ID}/position")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_missing_key_is_401_when_configured(monkeypatch, db_conn) -> None:
    monkeypatch.setenv(API_KEYS_ENV_VAR, "secret-key")
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        with TestClient(app) as client:
            response = client.get(f"/satellites/{TEST_NORAD_ID}/position")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_wrong_key_is_401(monkeypatch, db_conn) -> None:
    monkeypatch.setenv(API_KEYS_ENV_VAR, "secret-key")
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "wrong-key"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_valid_key_is_200(monkeypatch, db_conn) -> None:
    monkeypatch.setenv(API_KEYS_ENV_VAR, "secret-key")
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "secret-key"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_any_configured_key_is_accepted(monkeypatch, db_conn) -> None:
    monkeypatch.setenv(API_KEYS_ENV_VAR, "key-one, key-two")
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/satellites/{TEST_NORAD_ID}/position", headers={"X-API-Key": "key-two"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_health_always_open(monkeypatch) -> None:
    monkeypatch.setenv(API_KEYS_ENV_VAR, "secret-key")
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
