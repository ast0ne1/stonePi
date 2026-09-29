"""Quick create-watch smoke (mock mode)."""
from __future__ import annotations

import re
import os

os.environ.setdefault("PRICEWATCH_MOCK", "1")

from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    with TestClient(app) as client:
        page = client.get("/watches/new?q=Sony&product_id=1000000001&source_id=mock")
        assert page.status_code == 200
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert match, "csrf token missing"
        token = match.group(1)
        resp = client.post(
            "/watches/new",
            data={
                "csrf_token": token,
                "source_id": "mock",
                "product_id": "1000000001",
                "product_name": "Sony WH-1000XM6",
                "variant": "Black",
                "manufacturer": "Sony",
                "image_url": "",
                "product_url": "",
                "target_price": "2000",
                "condition": "new",
                "in_stock_required": "1",
                "schedule_minutes": "360",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.status_code
        loc = resp.headers.get("location") or "/"
        path = loc.split("?")[0]
        detail = client.get(path)
        assert detail.status_code == 200
        assert "strike" in detail.text.lower()
        print("ok", path)


if __name__ == "__main__":
    main()
