"""Trustpilot shop scores via Bright Data's "Trustpilot business reviews" scraper.

Modelled on EventTrakr's ``brightdata_facebook.py``: ``POST /datasets/v3/scrape``
answers synchronously (HTTP 200) inside Bright Data's ~1 minute window, otherwise
202 + ``snapshot_id`` → poll ``/progress`` → fetch ``/snapshot``.

Shape confirmed by a live test (2026-09-30):

- It is a per-review scraper: every record is one review with the company fields
  repeated. ``num_of_reviews_limit: 1`` keeps it to ~1 record per shop, but it is
  not strict (Elgiganten returned 3) — keep the first record per input.
- The 200 body is NDJSON (one object per line); each record echoes ``input.url``.
- A shop with no Trustpilot page comes back as an error record,
  ``{"error": "No data", "error_code": "dead_page", "input": {...}}``.
- ``company_overall_rating`` is a decimal (3.5, 4.3); ``company_total_reviews`` is
  the review count (its schema description is mislabelled).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.sources.base import normalise_domain

logger = logging.getLogger("pricewatch.brightdata")

API_BASE = "https://api.brightdata.com/datasets/v3"
TRUSTPILOT_DATASET_ID = "gd_lm5zmhwd2sni130p"
PROFILE_URL = "https://www.trustpilot.com/review/{domain}"
REQUEST_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 5.0
MAX_WAIT_SECONDS = 300.0


class BrightDataError(Exception):
    """``billed`` is False only when the job never started (the request was refused),
    so trust.py hands its reserved records back to the shared limit."""

    def __init__(self, message: str, *, billed: bool = True):
        super().__init__(message)
        self.billed = billed


@dataclass
class TrustResult:
    status: str  # ok | not_found | error
    score: float | None = None
    review_count: int | None = None
    url: str | None = None
    website: str | None = None
    error: str | None = None


def profile_url(domain: str) -> str:
    return PROFILE_URL.format(domain=domain)


def fetch_scores(api_key: str, domains: list[str]) -> tuple[dict[str, TrustResult], int]:
    """Look up each domain's Trustpilot score. Returns (results by domain, records used).

    Raises BrightDataError when the request itself fails; per-shop problems come
    back as ``error``/``not_found`` results.
    """
    wanted = [d for d in dict.fromkeys(domains) if d]
    if not wanted:
        return {}, 0
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"input": [{"url": profile_url(d), "num_of_reviews_limit": 1} for d in wanted]}
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            resp = client.post(
                f"{API_BASE}/scrape",
                params={"dataset_id": TRUSTPILOT_DATASET_ID, "notify": "false", "include_errors": "true"},
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise BrightDataError(f"Bright Data scrape failed: HTTP {e.response.status_code}", billed=False) from e
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data scrape request failed: {e}", billed=False) from e

        if resp.status_code == 202:
            snapshot_id = _snapshot_id(resp.text)
            if not snapshot_id:
                raise BrightDataError("Bright Data returned 202 without a snapshot_id")
            records = _wait_for_snapshot(client, headers, snapshot_id)
        else:
            records = parse_records(resp.text)
    return map_records(wanted, records), len(records)


def _snapshot_id(text: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data.get("snapshot_id") if isinstance(data, dict) else None


def _wait_for_snapshot(client: httpx.Client, headers: dict, snapshot_id: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        try:
            progress = client.get(f"{API_BASE}/progress/{snapshot_id}", headers=headers)
            progress.raise_for_status()
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data progress check failed: {e}") from e
        status = (progress.json() or {}).get("status")
        if status == "ready":
            break
        if status in ("failed", "canceled"):
            raise BrightDataError(f"Bright Data job {status}")
        if time.monotonic() > deadline:
            raise BrightDataError("Timed out waiting for Bright Data snapshot")
        time.sleep(POLL_INTERVAL_SECONDS)
    try:
        data = client.get(f"{API_BASE}/snapshot/{snapshot_id}", params={"format": "json"}, headers=headers)
        data.raise_for_status()
    except httpx.HTTPError as e:
        raise BrightDataError(f"Bright Data snapshot fetch failed: {e}") from e
    return parse_records(data.text)


def parse_records(text: str) -> list[dict[str, Any]]:
    """Accept a JSON array, a single object, or NDJSON."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping unparseable Bright Data line")
                continue
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return [data] if isinstance(data, dict) else []


def _record_domain(record: dict[str, Any]) -> str | None:
    source = record.get("input") if isinstance(record.get("input"), dict) else {}
    url = str(source.get("url") or record.get("url") or "")
    marker = "/review/"
    if marker not in url:
        return None
    return normalise_domain(url.split(marker, 1)[1].split("?", 1)[0])


def map_records(domains: list[str], records: list[dict[str, Any]]) -> dict[str, TrustResult]:
    results: dict[str, TrustResult] = {}
    for record in records:
        domain = _record_domain(record)
        if not domain or domain in results and results[domain].status == "ok":
            continue
        if record.get("error") or record.get("error_code"):
            code = str(record.get("error_code") or "")
            if code == "dead_page":
                results.setdefault(domain, TrustResult(status="not_found"))
            else:
                results.setdefault(
                    domain, TrustResult(status="error", error=str(record.get("error") or code or "Lookup failed"))
                )
            continue
        score = _float(record.get("company_overall_rating"))
        count = _int(record.get("company_total_reviews"))
        if score is None or score <= 0:
            results[domain] = TrustResult(status="not_found")
            continue
        results[domain] = TrustResult(
            status="ok",
            score=score,
            review_count=count,
            url=str(record.get("url") or profile_url(domain)).split("?", 1)[0],
            website=record.get("company_website"),
        )
    for domain in domains:
        results.setdefault(domain, TrustResult(status="not_found"))
    return results


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
