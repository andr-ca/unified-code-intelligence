import os

MAX_DISCOUNT = os.getenv("MAX_DISCOUNT", "50")


class DiscountRule:
    def apply(self, cart):
        return sum(item["price"] for item in cart)


class PricingCalculator:
    def calculate(self, cart):
        rule = DiscountRule()
        return rule.apply(cart)
