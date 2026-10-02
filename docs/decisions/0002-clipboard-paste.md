# 0002 — Insert text by pasting from the clipboard

**Status:** Accepted

## Context
After transcription the text must appear in whatever app has focus. Two approaches:
1. **Simulate typing**, sending one key event per character.
2. **Paste**: put the text on the clipboard and send Cmd+V.

## Decision
Paste via the clipboard, saving the user's clipboard first and restoring it afterwards (`ClipboardInserter`).

## Consequences
- ✅ Fast and reliable for long text; one key event instead of hundreds
- ✅ Handles emoji, accents and non-Latin scripts that key-by-key typing often gets wrong
- ✅ The user's clipboard is restored, even if the paste fails
- ⚠️ Briefly overwrites the clipboard; clipboard managers may record the transcript
- ⚠️ Only text contents are preserved; a copied image or file is lost
- ⚠️ A short delay (0.15 s) is needed so the target app reads the clipboard before it is restored
- ⚠️ Needs macOS Accessibility permission to send Cmd+V
- The `inserter` module is the only place that knows this, so a typing-based inserter can replace it later.
