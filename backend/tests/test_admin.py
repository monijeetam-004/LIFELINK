from __future__ import annotations

import pytest

from app.api.routes import admin as admin_routes
from app.main import app
from tests.helpers import auth_client

# The admin router is not yet wired into app/main.py (that happens centrally,
# together with everyone else's routers, to avoid migration-chain conflicts).
# Register it here so this test module can exercise it end-to-end; this does
# not modify app/main.py itself.
if not any(getattr(r, "path", "").startswith("/admin") for r in app.router.routes):
    app.include_router(admin_routes.router)

NON_ADMIN_ROLES = ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"]


# --- 403 for every non-admin role, on every endpoint ---------------------- #


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_list_users_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/users").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_user_stats_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/users/stats").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_role_change_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    other, _ = auth_client("PATIENT")
    other_id = other.get("/auth/me").json()["id"]
    assert c.patch(f"/admin/users/{other_id}/role", json={"role": "DONOR"}).status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_suspend_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    other, _ = auth_client("PATIENT")
    other_id = other.get("/auth/me").json()["id"]
    resp = c.post(f"/admin/users/{other_id}/suspend", json={"reason": "abuse"})
    assert resp.status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_reactivate_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    other, _ = auth_client("PATIENT")
    other_id = other.get("/auth/me").json()["id"]
    assert c.post(f"/admin/users/{other_id}/reactivate").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_blood_requests_list_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/blood-requests").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_emergencies_list_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/emergencies").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_audit_log_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/audit-log").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_system_health_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/system/health").status_code == 403


# --- users directory + stats ---------------------------------------------- #


def test_user_directory_search_and_filter() -> None:
    admin, _ = auth_client("ADMIN")
    auth_client("PATIENT", email="alice@example.com", name="Alice Alpha")
    auth_client("DONOR", email="bob@example.com", name="Bob Beta")

    r = admin.get("/admin/users", params={"query": "alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "alice@example.com"
    assert body["items"][0]["role"] == "PATIENT"

    r = admin.get("/admin/users", params={"role": "DONOR"})
    assert r.status_code == 200
    assert any(u["email"] == "bob@example.com" for u in r.json()["items"])
    assert all(u["role"] == "DONOR" for u in r.json()["items"])


def test_user_directory_pagination_shape() -> None:
    admin, _ = auth_client("ADMIN")
    for i in range(3):
        auth_client("PATIENT", email=f"pager{i}@example.com")
    r = admin.get("/admin/users", params={"page": 1, "page_size": 2})
    body = r.json()
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 4  # 3 patients + the admin itself


def test_user_stats_are_real_counts_that_change() -> None:
    admin, _ = auth_client("ADMIN")
    before = admin.get("/admin/users/stats").json()

    auth_client("PATIENT", email="statcheck@example.com")

    after = admin.get("/admin/users/stats").json()
    assert after["total_users"] == before["total_users"] + 1
    assert after["by_role"]["PATIENT"] == before["by_role"]["PATIENT"] + 1
    assert after["active_count"] == before["active_count"] + 1
    assert after["suspended_count"] == before["suspended_count"]


# --- role change ------------------------------------------------------------ #


def test_role_change_updates_and_audits() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("DONOR")
    target_id = target.get("/auth/me").json()["id"]

    r = admin.patch(f"/admin/users/{target_id}/role", json={"role": "PATIENT"})
    assert r.status_code == 200
    assert r.json()["role"] == "PATIENT"

    log = admin.get("/admin/audit-log", params={"action": "admin.user_role_changed"}).json()
    assert any(
        e["target_id"] == target_id and e["meta"]["new_role"] == "PATIENT"
        for e in log["items"]
    )


def test_admin_cannot_change_own_role() -> None:
    admin, _ = auth_client("ADMIN")
    admin_id = admin.get("/auth/me").json()["id"]
    r = admin.patch(f"/admin/users/{admin_id}/role", json={"role": "PATIENT"})
    assert r.status_code == 400


# --- suspend / reactivate --------------------------------------------------- #


def test_suspend_requires_reason() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("PATIENT")
    target_id = target.get("/auth/me").json()["id"]

    # missing reason entirely
    assert admin.post(f"/admin/users/{target_id}/suspend", json={}).status_code == 422
    # too-short reason
    assert admin.post(
        f"/admin/users/{target_id}/suspend", json={"reason": "ab"}
    ).status_code == 422


def test_suspend_blocks_next_authenticated_request_and_reactivate_restores() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("PATIENT")
    target_id = target.get("/auth/me").json()["id"]

    assert target.get("/auth/me").status_code == 200

    r = admin.post(
        f"/admin/users/{target_id}/suspend", json={"reason": "policy violation"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is False
    assert body["suspended_reason"] == "policy violation"

    # suspended user is now rejected on their very next authenticated request
    assert target.get("/auth/me").status_code == 401

    log = admin.get("/admin/audit-log", params={"action": "admin.user_suspended"}).json()
    assert any(
        e["target_id"] == target_id and e["meta"]["reason"] == "policy violation"
        for e in log["items"]
    )

    r = admin.post(f"/admin/users/{target_id}/reactivate")
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is True
    assert body["suspended_reason"] is None

    # target can log in again and act
    relogin = admin.get("/admin/users", params={"query": target_id})
    assert relogin.status_code == 200

    log2 = admin.get("/admin/audit-log", params={"action": "admin.user_reactivated"}).json()
    assert any(e["target_id"] == target_id for e in log2["items"])


def test_admin_cannot_suspend_self() -> None:
    admin, _ = auth_client("ADMIN")
    admin_id = admin.get("/auth/me").json()["id"]
    r = admin.post(f"/admin/users/{admin_id}/suspend", json={"reason": "self test"})
    assert r.status_code == 400


# --- blood-requests / emergencies list views -------------------------------- #


def test_admin_blood_requests_list_reflects_real_rows() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    before = admin.get("/admin/blood-requests").json()

    patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})

    after = admin.get("/admin/blood-requests").json()
    assert len(after) == len(before) + 1

    filtered = admin.get("/admin/blood-requests", params={"status": "PENDING"}).json()
    assert all(x["status"] == "PENDING" for x in filtered)


def test_admin_emergencies_list_reflects_real_rows() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    before = admin.get("/admin/emergencies").json()

    patient.post("/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro ER"})

    after = admin.get("/admin/emergencies").json()
    assert len(after) == len(before) + 1


# --- audit log --------------------------------------------------------------- #


def test_audit_log_lists_real_entries_with_pagination() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("PATIENT")
    target_id = target.get("/auth/me").json()["id"]
    admin.post(f"/admin/users/{target_id}/suspend", json={"reason": "test entry"})

    r = admin.get("/admin/audit-log", params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None

    r2 = admin.get("/admin/audit-log", params={"limit": 1, "cursor": body["next_cursor"]})
    assert r2.status_code == 200
    assert r2.json()["items"][0]["id"] != body["items"][0]["id"]


def test_audit_log_pages_never_repeat_although_reading_appends_entries() -> None:
    """Reading the log writes `admin.audit_log_viewed` at the top; a keyset
    cursor must still walk older entries without repeats or gaps."""
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("PATIENT")
    target_id = target.get("/auth/me").json()["id"]
    for i in range(3):
        admin.post(f"/admin/users/{target_id}/suspend", json={"reason": f"entry {i}"})
        admin.post(f"/admin/users/{target_id}/reactivate", json={"reason": f"entry {i}"})
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"limit": 2, "target_type": "user"}
        if cursor:
            params["cursor"] = cursor
        body = admin.get("/admin/audit-log", params=params).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)), "a page repeated an entry"
    assert admin.get("/admin/audit-log", params={"cursor": "17"}).status_code == 400


# --- system health ------------------------------------------------------------ #


def test_system_health_reports_real_component_status() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db"]["ok"] is True
    assert body["redis"]["ok"] is True
    assert body["storage"]["ok"] is True

    # The graph component's `configured`/`connected` depend on this
    # environment's NEO4J_* -- honestly reported, not assumed. Overall
    # `status` must track that: "ok" iff nothing is actually broken, where
    # "not configured" is a supported feature-off state (not a problem) but
    # "configured but unreachable" is.
    graph = body["graph"]
    assert set(graph.keys()) >= {"configured", "connected", "status"}
    graph_problem = graph["configured"] and not graph["connected"]
    expected_status = "ok" if not graph_problem else "degraded"
    assert body["status"] == expected_status

    # SMS is off by default in every LifeLink deployment (no MSG91_* set in
    # the test environment) -- this must read as a normal, supported
    # "feature off" state, exactly like the graph engine, never a fault,
    # and it must never claim delivery is available.
    sms = body["sms"]
    assert set(sms.keys()) >= {"configured", "status", "provider", "recent_failures"}
    assert sms["configured"] is False
    assert sms["status"] == "disabled"
    assert sms["provider"] == "MSG91"
    assert sms["recent_failures"] == 0
