"""Pricing rules: base calculator and a discount rule."""


class BaseCalculator:
    """Abstract base for all calculators."""

    def calculate(self, cart):
        raise NotImplementedError


class DiscountRule:
    """Applies a simple discount by summing item prices."""

    def apply(self, cart):
        return sum(item["price"] for item in cart)
