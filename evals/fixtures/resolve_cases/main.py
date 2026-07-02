from dog import Dog
from helpers import helper


def run():
    d = Dog()
    return d.speak() + helper()
