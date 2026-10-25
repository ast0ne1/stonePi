from stonepi_audit import ALLOWED, AuditLog, denied, summarise


def test_summarise_caps_drops_secrets_and_flattens():
    out = summarise({"days": 3, "query": "x" * 200, "token": "abc", "api_key": "k", "items": [1, 2], "ok": True})
    assert out["days"] == 3 and out["ok"] is True
    assert len(out["query"]) == 80 and out["query"].endswith("…")
    assert "token" not in out and "api_key" not in out
    assert out["items"] == "<list:2>"


def test_record_and_query(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.record(actor="binding:1", subject="adam", action="get_status", decision=ALLOWED, size=10, now=1000)
    log.record(actor="binding:2", subject="eva", action="ask", decision=denied("away_disabled"), now=1001)
    rows = log.query()
    assert [r["action"] for r in rows] == ["ask", "get_status"]
    assert log.query(actor="binding:1")[0]["subject"] == "adam"
    assert log.query(decision="DENIED")[0]["decision"] == "DENIED:away_disabled"
    assert log.query(since=1000.5)[0]["actor"] == "binding:2"


def test_retention_prunes_old_rows(tmp_path):
    log = AuditLog(tmp_path / "audit.db", retention_days=1)
    log.record(actor="a", action="x", decision=ALLOWED, now=1000)
    log.record(actor="a", action="y", decision=ALLOWED, now=1000 + 2 * 86400)
    assert [r["action"] for r in log.query()] == ["y"]
