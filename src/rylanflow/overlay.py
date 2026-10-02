"""The floating dictation pop-up: a small borderless panel near the bottom (or top) of the
screen, showing a level meter while recording and a pulsing dot trio while transcribing.

Main thread only, like dashboard/window.py. All the actual state/animation logic lives in
overlay_model.OverlayModel (pure, unit-tested); this module only draws whatever Frame it hands
back and manages a 30fps NSTimer that runs only while something needs to be shown on screen.
"""

import logging
import math
import time

import AppKit
import objc
from Foundation import NSMakeRect

from rylanflow.overlay_model import OverlayModel

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 120, 34
MARGIN = 24  # distance from the top or bottom edge of the screen's visible area
FPS = 30
BAR_WIDTH = 3
BAR_GAP = 3
DOT_DIAMETER = 7


class _OverlayView(AppKit.NSView):
    """Pure drawing surface. setFrameData_ is the only thing driving what it shows."""

    def initWithFrame_(self, frame):
        self = objc.super(_OverlayView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._frame_data = None
        return self

    def setFrameData_(self, frame_data) -> None:
        self._frame_data = frame_data
        self.setNeedsDisplay_(True)

    def isOpaque(self) -> bool:
        return False

    def drawRect_(self, _rect) -> None:
        data = self._frame_data
        if data is None:
            return
        bounds = self.bounds()
        if data.mode == "recording":
            _draw_recording(bounds, data.bars)
        elif data.mode == "working":
            _draw_working(bounds, data.pulse_phase)


def _draw_recording(bounds, bars: list[float]) -> None:
    dot_rect = NSMakeRect(10, (bounds.size.height - DOT_DIAMETER) / 2, DOT_DIAMETER, DOT_DIAMETER)
    AppKit.NSColor.systemRedColor().set()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

    n = len(bars)
    total_width = n * BAR_WIDTH + (n - 1) * BAR_GAP
    start_x = bounds.size.width - total_width - 14
    max_height = bounds.size.height - 12
    AppKit.NSColor.whiteColor().set()
    for i, level in enumerate(bars):
        h = max(2.0, level * max_height)
        x = start_x + i * (BAR_WIDTH + BAR_GAP)
        y = (bounds.size.height - h) / 2
        rect = NSMakeRect(x, y, BAR_WIDTH, h)
        radius = BAR_WIDTH / 2
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius).fill()


def _draw_working(bounds, pulse_phase: float) -> None:
    n = 3
    gap = 8
    total_width = n * DOT_DIAMETER + (n - 1) * gap
    start_x = (bounds.size.width - total_width) / 2
    center_y = bounds.size.height / 2
    AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.9).set()
    for i in range(n):
        phase = (pulse_phase + i / n) % 1.0
        scale = 0.55 + 0.45 * abs(math.sin(phase * math.pi))
        d = DOT_DIAMETER * scale
        x = start_x + i * (DOT_DIAMETER + gap) + (DOT_DIAMETER - d) / 2
        y = center_y - d / 2
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, y, d, d)).fill()


class Overlay:
    """Owns the panel and its animation timer. Call set_sources() once, then ensure_running()
    whenever the pipeline leaves idle -- it keeps itself running (and stops itself) after that,
    based on what OverlayModel says each frame."""

    def __init__(self, position: str = "bottom", clock=time.monotonic) -> None:
        self._position = position
        self._clock = clock
        self._model = OverlayModel()
        self._state_fn = lambda: "idle"
        self._level_fn = lambda: 0.0
        self._panel = None
        self._view = None
        self._timer = None

    def set_sources(self, state_fn, level_fn) -> None:
        self._state_fn = state_fn
        self._level_fn = level_fn

    def set_position(self, position: str) -> None:
        self._position = position

    def ensure_running(self) -> None:
        """Cheap to call repeatedly; only does anything the first time after the timer stops."""
        if self._timer is None:
            self._start_timer()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)

    # --- internals, main thread only ---

    def _start_timer(self) -> None:
        def tick(_timer) -> None:
            self._tick()

        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0 / FPS, True, tick
        )
        # Keeps firing while the user is in a menu or live-resizing another window.
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, AppKit.NSRunLoopCommonModes
        )

    def _tick(self) -> None:
        frame = self._model.update(self._state_fn(), self._level_fn(), self._clock())
        if not frame.visible:
            self.stop()
            return
        if self._panel is None:
            self._build()
        self._position_panel()
        self._view.setFrameData_(frame)
        self._panel.orderFrontRegardless()  # shows it without taking focus from the dictated app

    def _build(self) -> None:
        rect = NSMakeRect(0, 0, WIDTH, HEIGHT)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(17.0)
        effect.layer().setMasksToBounds_(True)

        view = _OverlayView.alloc().initWithFrame_(rect)
        view.setWantsLayer_(True)
        effect.addSubview_(view)
        panel.setContentView_(effect)

        self._panel = panel
        self._view = view

    def _position_panel(self) -> None:
        mouse = AppKit.NSEvent.mouseLocation()
        screen = next(
            (s for s in AppKit.NSScreen.screens() if AppKit.NSMouseInRect(mouse, s.frame(), False)),
            AppKit.NSScreen.mainScreen(),
        )
        visible = screen.visibleFrame()
        x = visible.origin.x + (visible.size.width - WIDTH) / 2
        if self._position == "top":
            y = visible.origin.y + visible.size.height - MARGIN - HEIGHT
        else:
            y = visible.origin.y + MARGIN
        self._panel.setFrameOrigin_((x, y))
