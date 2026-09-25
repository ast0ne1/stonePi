from __future__ import annotations

from app.collectors.channels import channel_from_icon_src, channels_from_icon_srcs


def test_channel_from_fox_footy_icon():
    src = "/images/channel-live-guide/fox-footy-australia-tv-guide-live-sm.png"
    assert channel_from_icon_src(src) == "Fox Footy"


def test_channel_from_kayo_sports_icon():
    src = "/images/channel-live-guide/kayo-sports-australia-tv-guide-live-sm.png"
    assert channel_from_icon_src(src) == "Kayo Sports"


def test_channel_from_absolute_url():
    src = "https://ausportguide.com/images/channel-live-guide/7plus-australia-tv-guide-live-sm.png"
    assert channel_from_icon_src(src) == "7plus"


def test_channels_from_icon_srcs_dedupes():
    srcs = [
        "/images/channel-live-guide/fox-footy-australia-tv-guide-live-sm.png",
        "/images/channel-live-guide/fox-footy-australia-tv-guide-live-sm.png",
        "/images/channel-live-guide/kayo-sports-australia-tv-guide-live-sm.png",
    ]
    assert channels_from_icon_srcs(srcs) == ["Fox Footy", "Kayo Sports"]


def test_channel_from_icon_src_ignores_empty():
    assert channel_from_icon_src("") is None
    assert channel_from_icon_src(None) is None
