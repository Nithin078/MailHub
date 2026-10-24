import os

os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.services.seed import seed_demo


def test_demo_inbox_shows_fraud_and_hides_bank_debits(db):
    user = seed_demo(db)
    db.commit()

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            health = client.get("/api/health")
            assert health.status_code == 200
            login = client.post("/api/auth/demo")
            assert login.status_code == 200
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            feed = client.get("/api/feed?lane=all", headers=headers)
            assert feed.status_code == 200
            subjects = [item["message"]["subject"] for item in feed.json()["items"] if item["type"] == "message"]
            assert any("suspicious" in subject.lower() for subject in subjects)
            assert not any("debited" in subject.lower() for subject in subjects)

            silenced = client.get("/api/feed?lane=silenced", headers=headers)
            silenced_subjects = [item["message"]["subject"] for item in silenced.json()["items"] if item["type"] == "message"]
            assert any("debited" in subject.lower() for subject in silenced_subjects)

            settings = client.get("/api/settings", headers=headers)
            assert settings.json()["ai"]["enabled"] is False

            preview = client.post(
                "/api/rules/preview",
                headers=headers,
                json={
                    "conditions": {"all": [{"field": "sender_domain", "op": "eq", "value": "kotak.com"}]},
                    "exceptions": {"any": [{"field": "subject", "op": "regex", "value": "suspicious|fraud"}]},
                },
            )
            assert preview.status_code == 200
            assert preview.json()["matched"] >= 8

            denied = client.post("/api/gmail/push", json={"message": {"data": ""}})
            assert denied.status_code == 401
            accepted = client.post("/api/gmail/push?token=demo-push-token", json={"message": {"data": "e30="}})
            assert accepted.status_code == 204

            opps = client.get("/api/opportunities", headers=headers)
            assert any(row["company"] == "Accenture" for row in opps.json())
            accenture = next(row for row in opps.json() if row["company"] == "Accenture")
            assert len(accenture["events"]) >= 2
    finally:
        app.dependency_overrides.clear()
    assert user.email.endswith("inboxagent.local")
