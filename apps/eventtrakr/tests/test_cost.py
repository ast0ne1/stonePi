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


def test_pill_text_is_the_amount_for_paid_only():
    from app.services.cost import cost_pill_text, cost_pill_title

    assert cost_pill_text("Free") == "" and cost_pill_text("Unspecified") == "" and cost_pill_text("0 kr") == ""
    assert cost_pill_text("150 kr") == "150 kr"
    assert cost_pill_text("Tickets from DKK 150 at the door, students half") == "DKK 150"
    assert cost_pill_text("Tickets 120-180 kr incl. fees and a drink") == "120-180 kr"
    assert cost_pill_title("") == "Price not listed" and cost_pill_title("DKK 150") == "Price: DKK 150"
