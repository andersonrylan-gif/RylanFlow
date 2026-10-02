"""Global push-to-talk hotkey: fires callbacks when a key is pressed and released."""

from collections.abc import Callable

from pynput import keyboard


def parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Turn 'alt_r' or 'f13' into a pynput key; a single character works too."""
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"Unknown key: {name!r}") from None


class PushToTalk:
    """Calls on_press once when the key goes down and on_release when it comes up."""

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        key: str = "alt_r",  # Right Option
    ) -> None:
        self._key = parse_key(key)
        self._on_press = on_press
        self._on_release = on_release
        self._held = False
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def _press(self, key) -> None:
        if key == self._key and not self._held:  # ignore key auto-repeat
            self._held = True
            self._on_press()

    def _release(self, key) -> None:
        if key == self._key and self._held:
            self._held = False
            self._on_release()

    def set_key(self, name: str) -> None:
        """Change the hotkey while running."""
        self._key = parse_key(name)
        self._held = False

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def join(self) -> None:
        self._listener.join()
