"""Guards: versions and app lists come from one place (VERSION, __version__, APP_CATALOG).

See version-dehardcode.md. These read the monorepo, so they only run from a checkout.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from stonepi_auth import APP_CATALOG, APP_IDS, UPDATABLE_APP_IDS
from stonepi_auth.brand import asset_rev

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(not (ROOT / "apps").is_dir(), reason="needs the StonePi monorepo")

# A hand-typed cache-bust token: ?v=20260927b, ?v={{ app_version }}-20260926f, ?v=1.2.3
LITERAL_REV = re.compile(r"\?v=(?:\{\{[^}]*\}\}-)?[0-9][0-9A-Za-z.]*")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_templates_have_no_hand_typed_cache_bust_tokens():
    offenders = []
    for path in sorted(ROOT.glob("apps/*/app/templates/**/*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if LITERAL_REV.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "Use ?v={{ asset_rev }} / ?v={{ fonts_rev }}:\n" + "\n".join(offenders)


def test_asset_revs_are_not_hand_typed():
    for app_id in APP_IDS:
        init_py = ROOT / "apps" / app_id / "app" / "__init__.py"
        text = init_py.read_text(encoding="utf-8")
        assert not re.search(r'^__asset_rev__\s*=\s*["\']', text, re.M), f"{app_id}: __asset_rev__ is a literal"


def test_every_catalog_app_has_a_version():
    for app_id in APP_IDS:
        text = (ROOT / "apps" / app_id / "app" / "__init__.py").read_text(encoding="utf-8")
        assert re.search(r'^__version__\s*=\s*"[0-9]+(\.[0-9]+){2,3}"', text, re.M), app_id


def test_release_zip_ids_follow_catalog():
    sys.argv = ["build_release_zips.py"]
    module = _load(ROOT / "scripts" / "build_release_zips.py", "_build_release_zips")
    assert module.APP_IDS == UPDATABLE_APP_IDS
    shipped = [item["id"] for item in APP_CATALOG if item.get("ships_with") == "platform"]
    for app_id in shipped:
        assert f"apps/{app_id}" in module.PLATFORM_INCLUDE


def test_versions_table_lists_every_catalog_app():
    module = _load(ROOT / "scripts" / "build_release_zips.py", "_build_release_zips_table")
    table = module.versions_table()
    for app_id in APP_IDS:
        assert f"| {app_id}" in table


def test_recover_unit_list_matches_catalog():
    # Recover must start even when StonePi packages are broken, so it keeps its own list.
    tree = ast.parse((ROOT / "apps" / "recover" / "app" / "main.py").read_text(encoding="utf-8"))
    units = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "UNITS" for t in node.targets)
    )
    expected = {item["unit"] for item in APP_CATALOG if item["id"] != "recover"} | {"nginx"}
    assert set(units) == expected


def test_contract_app_labels_match_catalog_names():
    sys.path.insert(0, str(ROOT / "packages" / "stonepi_contracts"))
    try:
        from stonepi_contracts.catalog import APP_LABELS
    finally:
        sys.path.pop(0)
    for item in APP_CATALOG:
        if item["id"] in APP_LABELS:
            assert APP_LABELS[item["id"]] == item["name"], item["id"]
    user_apps = {item["id"] for item in APP_CATALOG if item.get("group") == "user"}
    assert user_apps <= set(APP_LABELS), sorted(user_apps - set(APP_LABELS))


def test_asset_rev_changes_with_static_files(tmp_path):
    (tmp_path / "app.css").write_text("a{}", encoding="utf-8")
    first = asset_rev(tmp_path)
    assert first == asset_rev(tmp_path)
    (tmp_path / "app.css").write_text("a{color:red}", encoding="utf-8")
    assert asset_rev(tmp_path) != first
    assert asset_rev(tmp_path / "missing") == asset_rev()
