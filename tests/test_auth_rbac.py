"""
Automated Unit & Integration Tests for SignalSync Authentication & RBAC.
Atharav Arun Futane (2023BCS033) & Jitesh Sitaram Gaikwad (2023BCS083)
"""
import pytest
from fastapi.testclient import TestClient

from app.auth.security import Role, authenticate_user, create_access_token
from app.database.audit import get_recent_audit_logs, log_activity
from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_password_auth_success():
    admin = authenticate_user("admin", "admin123")
    assert admin is not None
    assert admin.role == Role.ADMIN
    assert "Atharav" in admin.full_name

    operator = authenticate_user("operator", "operator123")
    assert operator is not None
    assert operator.role == Role.OPERATOR

    viewer = authenticate_user("viewer", "viewer123")
    assert viewer is not None
    assert viewer.role == Role.VIEWER


def test_password_auth_invalid():
    bad = authenticate_user("admin", "wrongpassword")
    assert bad is None
    unknown = authenticate_user("nonexistent", "123")
    assert unknown is None


def test_jwt_token_flow(client):
    # 1. Login with valid credentials
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["role"] == Role.ADMIN
    token = data["access_token"]

    # 2. Query /api/auth/me with bearer token
    me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    assert me_res.json()["username"] == "admin"


def test_rbac_endpoint_protection(client):
    # Unauthenticated / guest access to admin endpoint should fail with 403
    res_unauth = client.post("/api/traffic/thresholds", json={"t_min": 10, "t_max": 45})
    assert res_unauth.status_code == 403

    # Login as operator
    op_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    op_token = op_login.json()["access_token"]

    # Operator accessing operator-allowed endpoint succeeds
    op_ctrl = client.post(
        "/api/traffic/weather",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"weather": "rainy"}
    )
    assert op_ctrl.status_code == 200

    # Operator accessing admin-only endpoint (thresholds) fails with 403
    op_admin_ctrl = client.post(
        "/api/traffic/thresholds",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"t_min": 12, "t_max": 50}
    )
    assert op_admin_ctrl.status_code == 403

    # Login as admin
    admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_token = admin_login.json()["access_token"]

    # Admin accessing admin endpoint succeeds
    admin_ctrl = client.post(
        "/api/traffic/thresholds",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"t_min": 12, "t_max": 50}
    )
    assert admin_ctrl.status_code == 200


def test_audit_trail_logging(client):
    log_activity("tester", "ADMIN", "UNIT_TEST_ACTION", "Testing audit trail write")
    logs = get_recent_audit_logs(limit=10)
    assert len(logs) > 0
    assert any(l["action"] == "UNIT_TEST_ACTION" for l in logs)
