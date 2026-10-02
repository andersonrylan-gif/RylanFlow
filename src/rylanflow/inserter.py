"""Types text into the focused app by pasting it, then restores the user's clipboard."""

import time

import pyperclip
from pynput import keyboard

PASTE_SETTLE_SECONDS = 0.15  # let the target app read the clipboard before we restore it


class ClipboardInserter:
    def __init__(self) -> None:
        self._keyboard = keyboard.Controller()

    def insert(self, text: str) -> None:
        if not text:
            return
        previous = pyperclip.paste()
        pyperclip.copy(text)
        try:
            with self._keyboard.pressed(keyboard.Key.cmd):
                self._keyboard.tap("v")
            time.sleep(PASTE_SETTLE_SECONDS)
        finally:
            pyperclip.copy(previous)
