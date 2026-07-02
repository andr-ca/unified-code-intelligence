from base import Animal


class Dog(Animal):
    def speak(self):
        return "woof"

    def greet(self):
        return self.describe()
