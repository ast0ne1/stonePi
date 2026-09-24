from app.routers.ui import (
    ADMIN_ONLY_SETTINGS_TABS,
    SETTINGS_GROUPS,
    SETTINGS_SECTION_PANELS,
    SETTINGS_TAB_KEYS,
    normalize_settings_panel,
    normalize_settings_tab,
    normalize_settings_tab_for_role,
    settings_groups_for,
    settings_path,
    settings_section_panels_for,
    settings_tabs_for,
)


def test_settings_tab_defaults_and_aliases():
    assert normalize_settings_tab(None) == "device"
    assert normalize_settings_tab("") == "device"
    assert normalize_settings_tab("nope") == "device"
    assert normalize_settings_tab("access") == "device"
    assert normalize_settings_tab("LLM") == "llm"
    assert normalize_settings_tab("backup") == "backup"
    assert normalize_settings_tab("update") == "update"
    assert normalize_settings_tab("filters") == "filters"
    assert normalize_settings_tab("translation") == "translation"
    assert normalize_settings_tab("publication") == "publication"
    assert normalize_settings_tab("notifications") == "notifications"


def test_settings_path_keeps_known_tabs():
    assert settings_path("categories") == "/settings?tab=categories"
    assert settings_path("catalog") == "/settings?tab=catalog"
    assert settings_path("filters") == "/settings?tab=filters"
    assert settings_path("translation") == "/settings?tab=translation"
    assert settings_path("publication") == "/settings?tab=publication"
    assert settings_path("publication", panel="stories") == "/settings?tab=publication&panel=stories"
    assert settings_path("mystery") == "/settings?tab=device"
    assert settings_path("access") == "/settings?tab=device"
    assert SETTINGS_TAB_KEYS == {
        "device",
        "publication",
        "schedule",
        "filters",
        "translation",
        "llm",
        "reader",
        "notifications",
        "categories",
        "catalog",
        "users",
        "backup",
        "update",
        "about",
    }


def test_settings_groups_cover_every_tab_once():
    seen: list[str] = []
    for _group_id, _label, tab_keys in SETTINGS_GROUPS:
        seen.extend(tab_keys)
    assert sorted(seen) == sorted(SETTINGS_TAB_KEYS)
    assert len(seen) == len(set(seen))


def test_settings_groups_for_respects_role_and_platform():
    admin_groups = settings_groups_for("admin", platform_managed=False)
    admin_keys = [key for _gid, _label, rows in admin_groups for key, _name, _sub in rows]
    assert "users" in admin_keys
    assert "update" in admin_keys
    assert "llm" in admin_keys

    platform_keys = [
        key
        for _gid, _label, rows in settings_groups_for("admin", platform_managed=True)
        for key, _name, _sub in rows
    ]
    assert "users" not in platform_keys
    assert "update" not in platform_keys
    assert "backup" in platform_keys

    user_keys = [
        key
        for _gid, _label, rows in settings_groups_for("user", can_use_ntfy=True)
        for key, _name, _sub in rows
    ]
    assert "publication" in user_keys
    assert "llm" not in user_keys
    assert ADMIN_ONLY_SETTINGS_TABS.isdisjoint(user_keys)


def test_settings_section_panels_publication_and_device():
    pub = settings_section_panels_for("publication")
    assert [panel_id for panel_id, _label, _cards in pub] == ["naming", "stories", "topics", "x3"]
    topics = next(cards for panel_id, _label, cards in pub if panel_id == "topics")
    assert topics == ("topics-mix", "topics-opds")

    solo_admin = settings_section_panels_for("device", is_admin=True, platform_managed=False)
    assert [panel_id for panel_id, _label, _cards in solo_admin] == ["access", "network", "interface"]

    platform_admin = settings_section_panels_for("device", is_admin=True, platform_managed=True)
    assert [panel_id for panel_id, _label, _cards in platform_admin] == ["network", "interface"]

    user_device = settings_section_panels_for("device", is_admin=False, platform_managed=False)
    assert user_device == ()

    assert settings_section_panels_for("filters") == ()
    assert normalize_settings_panel("publication", None) is None
    assert normalize_settings_panel("publication", "") is None
    assert normalize_settings_panel("publication", "stories") == "stories"
    assert normalize_settings_panel("publication", "nope") == "naming"
    assert normalize_settings_panel("filters", "anything") is None


def test_multi_card_tabs_have_panel_maps():
    multi = {
        "device",
        "publication",
        "schedule",
        "catalog",
        "reader",
        "notifications",
        "users",
        "update",
    }
    assert set(SETTINGS_SECTION_PANELS) == multi
    for tab in ("filters", "translation", "llm", "categories", "backup", "about"):
        assert tab not in SETTINGS_SECTION_PANELS


def test_non_admin_settings_tabs_hide_household_controls():
    keys = {key for key, _label in settings_tabs_for("user", can_use_ntfy=True)}
    assert "llm" not in keys
    assert "backup" not in keys
    assert "users" not in keys
    assert "catalog" not in keys
    assert "schedule" not in keys
    assert "update" not in keys
    assert "publication" in keys
    assert "notifications" in keys
    assert "about" in keys
    assert ADMIN_ONLY_SETTINGS_TABS.isdisjoint(keys)
    assert normalize_settings_tab_for_role("llm", "user") == "device"
    assert normalize_settings_tab_for_role("reader", "user") == "reader"
    assert normalize_settings_tab_for_role("llm", "admin") == "llm"


def test_notifications_tab_requires_ntfy_permission():
    without = {key for key, _ in settings_tabs_for("user", can_use_ntfy=False)}
    with_ntfy = {key for key, _ in settings_tabs_for("user", can_use_ntfy=True)}
    assert "notifications" not in without
    assert "notifications" in with_ntfy
    assert normalize_settings_tab_for_role("notifications", "user", can_use_ntfy=False) == "device"
    assert normalize_settings_tab_for_role("notifications", "user", can_use_ntfy=True) == "notifications"
    assert "notifications" in {key for key, _ in settings_tabs_for("admin")}


def test_platform_managed_hides_users_and_update_for_admin():
    solo = {key for key, _ in settings_tabs_for("admin", platform_managed=False)}
    platform = {key for key, _ in settings_tabs_for("admin", platform_managed=True)}
    assert "users" in solo and "update" in solo
    assert "users" not in platform and "update" not in platform
    assert "backup" in platform
    assert normalize_settings_tab_for_role("users", "admin", platform_managed=True) == "device"
    assert normalize_settings_tab_for_role("update", "admin", platform_managed=True) == "device"
    assert normalize_settings_tab_for_role("users", "admin", platform_managed=False) == "users"
