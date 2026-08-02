"""安全 P0 API 测试：诊断鉴权 / 白名单 / 登录时序 / 注册限流。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(integration_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    resp = client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_diag_index_requires_auth(client):
    assert client.get("/api/v1/diag/index").status_code == 401


def test_diag_chunks_requires_auth(client):
    assert client.get("/api/v1/diag/chunks", params={"ids": "x"}).status_code == 401


def test_diag_static_mount_removed(client):
    assert client.get("/diagnostics/index.json").status_code == 404
    assert client.get("/tools/diagnostics.html").status_code == 404


def test_diag_index_admin_ok(client, admin_token):
    resp = client.get("/api/v1/diag/index",
                      headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
