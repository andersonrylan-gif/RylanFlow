import pytest

from rylanflow.cleanup import remove_fillers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Um, I think uh we should go.", "I think we should go."),
        ("So, umm, that works.", "So, that works."),
        ("This is fine.", "This is fine."),
        ("Hmm, maybe the drummer arrived.", "Hmm, maybe the drummer arrived."),
        ("Uh", ""),
    ],
)
def test_remove_fillers(text, expected):
    assert remove_fillers(text) == expected


from rylanflow.cleanup import MAX_SNIPPETS, apply_snippets, clean_snippets  # noqa: E402


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My email.", "me@example.com"),
        ("my   Email", "me@example.com"),
        ("Send it to my email, please.", "Send it to me@example.com, please."),
        ("my work email.", "work@example.com"),
        ("my emails are many.", "my emails are many."),
        ("Nothing to expand.", "Nothing to expand."),
    ],
)
def test_apply_snippets(text, expected):
    snippets = {"my email": "me@example.com", "my work email": "work@example.com"}
    assert apply_snippets(text, snippets) == expected


def test_apply_snippets_keeps_backslashes_literal():
    assert apply_snippets("path", {"path": r"C:\new\1"}) == r"C:\new\1"


def test_clean_snippets_trims_dedupes_and_caps():
    assert clean_snippets({" a ": " b ", "": "x", "c": "", "A": "dup"}) == {"a": "b"}
    assert len(clean_snippets({f"t{i}": "x" for i in range(500)})) == MAX_SNIPPETS
