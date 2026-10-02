"""Light transcript clean-up before insertion."""

import re

# Only unambiguous hesitation sounds; "er" or "hmm" can be real words or intentional.
_FILLERS = re.compile(r"\b(?:um+|uh+|uhm|erm)\b[,.]?\s*", re.IGNORECASE)


def remove_fillers(text: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", _FILLERS.sub("", text)).strip()
    if cleaned and text[:1].isupper():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned
