"""Demo library for Conclave smoke tests. Intentionally tiny."""


def add(a, b):
    return a + b


def is_palindrome(s):
    s = "".join(c.lower() for c in s if c.isalnum())
    return s == s[::-1]
