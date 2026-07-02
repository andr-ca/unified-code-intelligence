from pricing import PricingCalculator


def place_order(cart):
    calc = PricingCalculator()
    return calc.calculate(cart)
