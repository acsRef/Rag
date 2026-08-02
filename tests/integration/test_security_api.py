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


def test_whitelist_does_not_pollute_rule_exclusions(client, admin_token, integration_db):
    from app.store.db import get_db_ctx, PiiAlert, SensitiveRule

    headers = {"Authorization": f"Bearer {admin_token}"}
    with get_db_ctx() as session:
        session.add(PiiAlert(
            source_type="document", source_id="wl-test-doc",
            rule_name="cn_phone", matched_text="13800138000",
            context_snippet="联系人 13800138000", strategy="reject", status="pending",
        ))
        session.commit()
        alert_id = session.query(PiiAlert).filter_by(source_id="wl-test-doc").first().id
        before = session.query(SensitiveRule).filter_by(rule_name="cn_phone").first().exclusion_words

    resp = client.post(f"/api/v1/admin/pii-alerts/{alert_id}/whitelist", headers=headers)
    assert resp.status_code == 200, resp.text

    with get_db_ctx() as session:
        rule = session.query(SensitiveRule).filter_by(rule_name="cn_phone").first()
        alert = session.query(PiiAlert).filter_by(id=alert_id).first()
    assert "13800138000" not in (rule.exclusion_words or "")   # 真实号码不得进排除词
    assert rule.exclusion_words == before                       # 规则配置完全不变
    assert alert.status == "false_positive"                     # 告警本身正常关闭
