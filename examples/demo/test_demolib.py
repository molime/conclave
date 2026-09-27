from demolib import add, is_palindrome


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_palindrome():
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert not is_palindrome("hello")
