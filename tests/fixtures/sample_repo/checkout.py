"""Checkout flow that uses the pricing calculator."""

from pricing.calculator import PricingCalculator


def place_order(cart):
    calc = PricingCalculator()
    return calc.calculate(cart)
