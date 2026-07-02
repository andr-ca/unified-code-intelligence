from pricing import PricingCalculator


def test_calculate():
    calc = PricingCalculator()
    assert calc.calculate([{"price": 10}]) == 10
