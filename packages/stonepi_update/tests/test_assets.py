from stonepi_update import find_asset, is_newer, normalize_repo, parse_version
from stonepi_update.core import parse_asset_name


def test_normalize_repo():
    assert normalize_repo("owner/stonePi") == "owner/stonePi"
    assert normalize_repo("https://github.com/owner/stonePi") == "owner/stonePi"
    assert normalize_repo("https://github.com/owner/stonePi.git") == "owner/stonePi"
    assert normalize_repo("not a repo") == ""


def test_parse_asset_name_and_find_asset():
    assert parse_asset_name("stonepi-newscast-0.0.2.zip", "newscast") == "0.0.2"
    assert parse_asset_name("newscast-0.0.2.zip", "newscast") == "0.0.2"
    assert parse_asset_name("FileServe-1.2.0.zip", "fileserve") == "1.2.0"
    assert parse_asset_name("other-1.0.0.zip", "newscast") is None
    assert parse_asset_name("stonepi-platform-0.1.1.zip", "newscast") is None
    assets = [
        {"name": "readme.txt", "browser_download_url": "x"},
        {"name": "newscast-0.0.1.zip", "browser_download_url": "a"},
        {"name": "stonepi-newscast-0.0.3.zip", "browser_download_url": "b"},
        {"name": "newscast-0.0.2.zip", "browser_download_url": "c"},
    ]
    best = find_asset(assets, "newscast")
    assert best is not None
    assert best["parsed_version"] == "0.0.3"
    assert best["browser_download_url"] == "b"
    # Prefer stonepi- prefix when versions match
    tied = find_asset(
        [
            {"name": "newscast-0.0.4.zip", "browser_download_url": "legacy"},
            {"name": "stonepi-newscast-0.0.4.zip", "browser_download_url": "branded"},
        ],
        "newscast",
    )
    assert tied is not None
    assert tied["browser_download_url"] == "branded"


def test_is_newer_and_parse_version():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert is_newer("1.2.4", "1.2.3")
    assert not is_newer("1.2.3", "1.2.3")
    assert not is_newer("1.2.2", "1.2.3")
