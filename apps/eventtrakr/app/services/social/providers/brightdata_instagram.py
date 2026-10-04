from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from app.services import brightdata
from app.services.brightdata import BrightDataBudgetError, BrightDataError  # noqa: F401 (re-exported)
from app.services.social.types import RawSocialPost

logger = logging.getLogger("eventtrakr.social.brightdata_instagram")

API_BASE = "https://api.brightdata.com/datasets/v3"
# Instagram Posts dataset — discover_new by profile URL returns recent posts.
INSTAGRAM_POSTS_DATASET_ID = "gd_lk5ns7kz21pck8jpis"
REQUEST_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 3.0
MAX_WAIT_SECONDS = 300.0
# Cost control: never ask Bright Data for more than this many posts per check.
DEFAULT_NUM_OF_POSTS = 10
MAX_NUM_OF_POSTS = 20


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _profile_url(username: str) -> str:
    handle = (username or "").strip().lstrip("@")
    return f"https://www.instagram.com/{handle}/"


def _hashtags_from(rec: dict, caption: str) -> list[str]:
    tags: list[str] = []
    raw = rec.get("post_hashtags") or rec.get("hashtags") or []
    if isinstance(raw, list):
        tags.extend(str(t).lstrip("#") for t in raw if t)
    elif isinstance(raw, str) and raw.strip():
        tags.append(raw.strip().lstrip("#"))
    for token in (caption or "").split():
        if token.startswith("#") and len(token) > 1:
            tags.append(token.lstrip("#"))
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _image_urls(rec: dict) -> list[str]:
    urls: list[str] = []
    for key in ("image_url", "thumbnail", "display_url", "photo_url"):
        val = rec.get(key)
        if isinstance(val, str) and val.startswith("http"):
            urls.append(val)
    photos = rec.get("photos") or rec.get("images") or rec.get("carousel_media") or []
    if isinstance(photos, list):
        for item in photos:
            if isinstance(item, str) and item.startswith("http"):
                urls.append(item)
            elif isinstance(item, dict):
                for k in ("url", "image_url", "display_url"):
                    v = item.get(k)
                    if isinstance(v, str) and v.startswith("http"):
                        urls.append(v)
                        break
    # Dedupe
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _map_post(rec: dict, username: str) -> RawSocialPost | None:
    external_id = str(
        rec.get("id")
        or rec.get("shortcode")
        or rec.get("pk")
        or rec.get("post_id")
        or ""
    ).strip()
    if not external_id:
        return None
    caption = str(rec.get("caption") or rec.get("description") or "").strip()
    post_url = str(rec.get("url") or rec.get("post_url") or "").strip()
    if not post_url:
        shortcode = rec.get("shortcode")
        if shortcode:
            post_url = f"https://www.instagram.com/p/{shortcode}/"
        else:
            post_url = _profile_url(username)
    published = _parse_dt(
        rec.get("datetime") or rec.get("date_posted") or rec.get("timestamp") or rec.get("taken_at")
    )
    media_type = str(rec.get("content_type") or rec.get("media_type") or "Photo").strip()
    return RawSocialPost(
        external_post_id=external_id,
        post_url=post_url,
        published_at=published,
        caption=caption,
        media_type=media_type,
        hashtags=_hashtags_from(rec, caption),
        image_urls=_image_urls(rec),
        metadata={"user_posted": rec.get("user_posted") or username},
    )


def _flatten_records(records: list) -> list[dict]:
    """Discover-by-profile may nest posts under a profile object."""
    posts: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        nested = rec.get("posts")
        if isinstance(nested, list) and nested:
            for item in nested:
                if isinstance(item, dict):
                    # Inherit account username hints
                    if "user_posted" not in item and rec.get("account"):
                        item = {**item, "user_posted": rec.get("account")}
                    posts.append(item)
            continue
        # Already a post-shaped record
        if rec.get("caption") is not None or rec.get("description") is not None or rec.get("shortcode"):
            posts.append(rec)
    return posts


def _wait_for_snapshot(client: httpx.Client, headers: dict, snapshot_id: str) -> list:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        progress_resp = client.get(f"{API_BASE}/progress/{snapshot_id}", headers=headers)
        progress_resp.raise_for_status()
        status = progress_resp.json().get("status")
        if status == "ready":
            break
        if status in ("failed", "canceled"):
            raise BrightDataError(f"Bright Data job {status}")
        if time.monotonic() > deadline:
            raise BrightDataError("Timed out waiting for Bright Data snapshot")
        time.sleep(POLL_INTERVAL_SECONDS)

    data_resp = client.get(
        f"{API_BASE}/snapshot/{snapshot_id}",
        params={"format": "json"},
        headers=headers,
    )
    data_resp.raise_for_status()
    return data_resp.json()


def fetch_recent_posts(
    api_key: str,
    username: str,
    *,
    num_of_posts: int = DEFAULT_NUM_OF_POSTS,
    posts_to_not_include: list[str] | None = None,
    start_date: str | None = None,
    dataset_id: str = INSTAGRAM_POSTS_DATASET_ID,
    paced: bool = True,
) -> list[RawSocialPost]:
    """Discover recent Instagram posts for a public profile via Bright Data.

    Uses discover_new + discover_by=url with a capped num_of_posts so free-tier
    credits are not burned re-fetching large histories. ``paced`` (scheduled
    polls) keeps to the month's share of the shared Bright Data limit; a manual
    "Check now" passes False and only the hard limit applies.
    """
    handle = (username or "").strip().lstrip("@")
    if not handle:
        return []
    limit = max(1, min(MAX_NUM_OF_POSTS, int(num_of_posts or DEFAULT_NUM_OF_POSTS)))
    payload_item: dict = {
        "url": _profile_url(handle),
        "num_of_posts": limit,
    }
    if posts_to_not_include:
        payload_item["posts_to_not_include"] = list(posts_to_not_include)[:50]
    if start_date:
        payload_item["start_date"] = start_date

    def call() -> list:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            try:
                resp = client.post(
                    f"{API_BASE}/scrape",
                    params={
                        "dataset_id": dataset_id,
                        "notify": "false",
                        "include_errors": "true",
                        "type": "discover_new",
                        "discover_by": "url",
                    },
                    headers=headers,
                    json={"input": [payload_item]},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise BrightDataError(f"Bright Data scrape failed: HTTP {e.response.status_code}", billed=False) from e
            except httpx.HTTPError as e:
                raise BrightDataError(f"Bright Data scrape request failed: {e}", billed=False) from e

            try:
                if resp.status_code == 202:
                    snapshot_id = resp.json().get("snapshot_id")
                    if not snapshot_id:
                        raise BrightDataError("Bright Data returned 202 without a snapshot_id")
                    records = _wait_for_snapshot(client, headers, snapshot_id)
                else:
                    records = resp.json()
            except httpx.HTTPError as e:
                raise BrightDataError(f"Bright Data snapshot fetch failed: {e}") from e
        return records if isinstance(records, list) else []

    # Posts can come back nested under one profile record; count whichever is larger.
    records = brightdata.metered(
        "instagram",
        limit,
        call,
        paced=paced,
        count=lambda recs: max(len(recs), len(_flatten_records(recs))),
    )

    raw_list = records if isinstance(records, list) else []
    flat = _flatten_records(raw_list)
    logger.info(
        "Bright Data Instagram: %d raw / %d flat post(s) for @%s",
        len(raw_list),
        len(flat),
        handle,
    )
    out: list[RawSocialPost] = []
    for rec in flat:
        mapped = _map_post(rec, handle)
        if mapped:
            out.append(mapped)
    return out


class BrightDataInstagramProvider:
    platform = "instagram"

    def __init__(self, api_key: str, *, dataset_id: str | None = None):
        self.api_key = api_key
        self.dataset_id = dataset_id or INSTAGRAM_POSTS_DATASET_ID

    def fetch_recent_posts(
        self,
        username: str,
        *,
        num_of_posts: int = DEFAULT_NUM_OF_POSTS,
        posts_to_not_include: list[str] | None = None,
        start_date: str | None = None,
        paced: bool = True,
    ) -> list[RawSocialPost]:
        return fetch_recent_posts(
            self.api_key,
            username,
            num_of_posts=num_of_posts,
            posts_to_not_include=posts_to_not_include,
            start_date=start_date,
            dataset_id=self.dataset_id,
            paced=paced,
        )
