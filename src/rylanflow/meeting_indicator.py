"""A small, always-visible floating panel shown while a meeting is being recorded -- so it's
obvious at a glance that RylanFlow is actually capturing, not just that someone clicked a menu
item or that auto-record silently decided to start. Sits top-right, distinct from the dictation
pop-up (overlay.py), since both can be visible at once. Deliberately understated (small, no red
dot, no "Recording" text) -- a few animated bars plus the elapsed time, closer to a quiet status
chip than an alarm. Main thread only, same pattern as overlay.py: build the panel lazily,
position it, and drive it with an NSTimer.
"""

import math
import time

import AppKit
import objc
from Foundation import NSMakeRect

WIDTH, HEIGHT = 92, 26
MARGIN = 16  # distance from the top-right corner of the screen's visible area
FPS = 12
BAR_COUNT = 4
BAR_WIDTH = 2.5
BAR_GAP = 3


class _IndicatorView(AppKit.NSView):
    """Pure drawing surface: a few animated bars plus whatever label setState_ last gave it."""

    def initWithFrame_(self, frame):
        self = objc.super(_IndicatorView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._label = ""
        self._phase = 0.0
        return self

    def updateLabel_phase_(self, label: str, phase: float) -> None:
        self._label = label
        self._phase = phase
        self.setNeedsDisplay_(True)

    def isOpaque(self) -> bool:
        return False

    def drawRect_(self, _rect) -> None:
        bounds = self.bounds()
        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = 10
        max_height = bounds.size.height - 10
        color = AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.85)
        color.set()
        for i in range(BAR_COUNT):
            phase = (self._phase + i / BAR_COUNT) % 1.0
            level = 0.3 + 0.7 * abs(math.sin(phase * math.pi))
            h = max(3.0, level * max_height)
            x = start_x + i * (BAR_WIDTH + BAR_GAP)
            y = (bounds.size.height - h) / 2
            rect = NSMakeRect(x, y, BAR_WIDTH, h)
            radius = BAR_WIDTH / 2
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, radius, radius
            ).fill()

        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
                11, AppKit.NSFontWeightMedium
            ),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        text = AppKit.NSString.stringWithString_(self._label)
        size = text.sizeWithAttributes_(attrs)
        text.drawAtPoint_withAttributes_(
            (start_x + total_width + 10, (bounds.size.height - size.height) / 2), attrs
        )


class MeetingIndicator:
    """Owns the panel and an animation timer. start() is idempotent (a no-op if already
    running); call it and stop() from the main thread only."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._panel = None
        self._view = None
        self._timer = None
        self._started_at: float | None = None

    def start(self) -> None:
        if self._timer is not None:
            return
        self._started_at = self._clock()
        if self._panel is None:
            self._build()
        self._position_panel()
        self._tick()
        self._panel.orderFrontRegardless()  # visible without stealing focus from the meeting app

        def tick(_timer) -> None:
            self._tick()

        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0 / FPS, True, tick
        )
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, AppKit.NSRunLoopCommonModes
        )

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)
        self._started_at = None

    # --- internals, main thread only ---

    def _tick(self) -> None:
        now = self._clock()
        elapsed = max(0, int(now - self._started_at)) if self._started_at else 0
        label = f"{elapsed // 60}:{elapsed % 60:02d}"
        self._view.updateLabel_phase_(label, now % 1.0)

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
        effect.layer().setCornerRadius_(13.0)
        effect.layer().setMasksToBounds_(True)

        view = _IndicatorView.alloc().initWithFrame_(rect)
        view.setWantsLayer_(True)
        effect.addSubview_(view)
        panel.setContentView_(effect)

        self._panel = panel
        self._view = view

    def _position_panel(self) -> None:
        screen = AppKit.NSScreen.mainScreen()
        visible = screen.visibleFrame()
        x = visible.origin.x + visible.size.width - WIDTH - MARGIN
        y = visible.origin.y + visible.size.height - HEIGHT - MARGIN
        self._panel.setFrameOrigin_((x, y))
