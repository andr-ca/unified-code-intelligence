"""Pricing calculator that composes discount rules."""

import os

from pricing.rules import BaseCalculator, DiscountRule

MAX_DISCOUNT = os.getenv("MAX_DISCOUNT", "50")


class PricingCalculator(BaseCalculator):
    """Computes the price of a cart by applying a discount rule."""

    def calculate(self, cart):
        rule = DiscountRule()
        return rule.apply(cart)
