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
