"""Dashboard → Settings Displays line counts the Car Thing as well as TRMNL (backlog B10)."""

from app.services import _displays_line


def test_trmnl_only_wording_unchanged():
    assert _displays_line({}) == "No screens yet"
    assert _displays_line({"count": 1, "pushing": 1}) == "1 screen · 1 pushing to TRMNL"
    assert _displays_line({"count": 2, "pushing": 2}) == "2 screens · all pushing to TRMNL"
    assert _displays_line({"count": 1, "pushing": 0}) == "1 screen · not pushing yet"
    # Car Thing switched off: not counted.
    off = {"enabled": False, "connected": False}
    assert _displays_line({"count": 1, "pushing": 1, "carthing": off}) == "1 screen · 1 pushing to TRMNL"


def test_car_thing_counted_while_on():
    on = {"enabled": True, "connected": True}
    assert _displays_line({"count": 1, "pushing": 1, "carthing": on}) == "2 screens · TRMNL pushing · Car Thing connected"
    assert _displays_line({"count": 0, "pushing": 0, "carthing": on}) == "1 screen · Car Thing connected"
    lost = {"enabled": True, "connected": False}
    assert (
        _displays_line({"count": 2, "pushing": 1, "carthing": lost})
        == "3 screens · 1 of 2 TRMNL pushing · Car Thing not connected"
    )
    assert _displays_line({"count": 1, "pushing": 0, "carthing": on}) == "2 screens · TRMNL not pushing yet · Car Thing connected"
