from app.services.cost import (
    cost_badge_class,
    cost_display_label,
    is_free_cost,
    is_unspecified_cost,
)


def test_legacy_combined_label_is_unspecified_not_free():
    assert is_unspecified_cost("Free / Unspecified")
    assert not is_free_cost("Free / Unspecified")
    assert cost_display_label("Free / Unspecified") == "Price: Unspecified"
    assert cost_badge_class("Free / Unspecified") == "cost-unspecified"


def test_explicit_free_and_paid():
    assert is_free_cost("Free")
    assert is_free_cost("free entry")
    assert not is_unspecified_cost("Free")
    assert cost_display_label("Free") == "Price: Free"
    assert cost_badge_class("Free") == "cost-free"

    assert not is_free_cost("DKK 250.00")
    assert not is_unspecified_cost("DKK 250.00")
    assert cost_display_label("DKK 250.00") == "Price: DKK 250.00"
    assert cost_badge_class("DKK 250.00") == "cost-paid"


def test_empty_is_unspecified():
    assert is_unspecified_cost(None)
    assert is_unspecified_cost("")
    assert cost_display_label("") == "Price: Unspecified"
