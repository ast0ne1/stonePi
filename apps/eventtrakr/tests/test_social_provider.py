"""Bright Data Instagram provider mapping tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.social.providers import brightdata_instagram as ig


def test_map_post_from_flat_record():
    rec = {
        "id": "3851176228498036124",
        "caption": "Quiz Night Friday",
        "url": "https://www.instagram.com/p/AbC/",
        "datetime": "2026-03-12T11:52:08.000Z",
        "content_type": "Photo",
        "image_url": "https://cdn.example/img.jpg",
        "post_hashtags": ["quiz"],
    }
    mapped = ig._map_post(rec, "theglobe")
    assert mapped is not None
    assert mapped.external_post_id == "3851176228498036124"
    assert mapped.caption == "Quiz Night Friday"
    assert "quiz" in mapped.hashtags
    assert mapped.image_urls == ["https://cdn.example/img.jpg"]


def test_flatten_nested_profile_posts():
    records = [
        {
            "account": "theglobe",
            "posts": [
                {"id": "1", "caption": "A", "datetime": "2026-01-01T00:00:00.000Z"},
                {"id": "2", "caption": "B", "datetime": "2026-01-02T00:00:00.000Z"},
            ],
        }
    ]
    flat = ig._flatten_records(records)
    assert len(flat) == 2
    assert flat[0]["user_posted"] == "theglobe"


def test_fetch_recent_posts_uses_discover_and_limit():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {
            "account": "theglobe",
            "posts": [
                {
                    "id": "99",
                    "caption": "Live tonight 20:00",
                    "datetime": "2026-09-26T10:00:00.000Z",
                    "image_url": "https://cdn.example/x.jpg",
                }
            ],
        }
    ]

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp

    with patch("app.services.social.providers.brightdata_instagram.httpx.Client", return_value=mock_client):
        posts = ig.fetch_recent_posts("fake-key", "theglobe", num_of_posts=5)

    assert len(posts) == 1
    assert posts[0].external_post_id == "99"
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == "https://api.brightdata.com/datasets/v3/scrape"
    params = call_kwargs[1]["params"]
    assert params["dataset_id"] == ig.INSTAGRAM_POSTS_DATASET_ID
    assert params["type"] == "discover_new"
    assert params["discover_by"] == "url"
    body = call_kwargs[1]["json"]
    assert body["input"][0]["num_of_posts"] == 5
    assert "theglobe" in body["input"][0]["url"]
