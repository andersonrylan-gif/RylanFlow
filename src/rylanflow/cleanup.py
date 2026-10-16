"""Light transcript clean-up before insertion."""

import re

# Only unambiguous hesitation sounds; "er" or "hmm" can be real words or intentional.
_FILLERS = re.compile(r"\b(?:um+|uh+|uhm|erm)\b[,.]?\s*", re.IGNORECASE)


MAX_SNIPPETS = 100


def clean_snippets(snippets: dict) -> dict[str, str]:
    """Trim triggers, drop blank triggers/expansions, cap the count (triggers match case-blind)."""
    cleaned: dict[str, str] = {}
    seen: set[str] = set()
    for trigger, expansion in snippets.items():
        trigger, expansion = str(trigger).strip(), str(expansion).strip()
        if trigger and expansion and trigger.lower() not in seen:
            seen.add(trigger.lower())
            cleaned[trigger] = expansion
    return dict(list(cleaned.items())[:MAX_SNIPPETS])


def apply_snippets(text: str, snippets: dict[str, str]) -> str:
    """Replace spoken trigger phrases with their expansion (Wispr Flow's "snippets").

    Triggers match whole words, ignoring case and the punctuation Whisper puts between words.
    Whisper also ends the sentence with a period; when the trigger is the end of the dictation
    that trailing punctuation is dropped so "my email." pastes the bare address.
    """
    # Longest first, so "my work email" wins over "my email" when both exist.
    for trigger in sorted(snippets, key=len, reverse=True):
        words = re.findall(r"\w+", trigger)
        if not words:
            continue
        body = r"[\s,.\-]+".join(re.escape(w) for w in words)
        pattern = re.compile(rf"(?<!\w){body}(?!\w)(?:[.,!?]*\s*$)?", re.IGNORECASE)
        # A function, not a string: expansions may contain backslashes.
        text = pattern.sub(lambda _m, expansion=snippets[trigger]: expansion, text)
    return text


def remove_fillers(text: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", _FILLERS.sub("", text)).strip()
    if cleaned and text[:1].isupper():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned
