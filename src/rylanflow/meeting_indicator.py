"""A small, always-visible floating panel shown while a meeting is being recorded -- so it's
obvious at a glance that RylanFlow is actually capturing, not just that someone clicked a menu
item or that auto-record silently decided to start. Sits top-right, distinct from the dictation
pop-up (overlay.py), since both can be visible at once. Main thread only, same pattern as
overlay.py: build the panel lazily, position it, and drive it with an NSTimer.
"""

import time

import AppKit
import objc
from Foundation import NSMakeRect

WIDTH, HEIGHT = 160, 34
MARGIN = 16  # distance from the top-right corner of the screen's visible area


class _IndicatorView(AppKit.NSView):
    """Pure drawing surface: a red dot plus whatever label setLabel_ last gave it."""

    def initWithFrame_(self, frame):
        self = objc.super(_IndicatorView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._label = ""
        return self

    def setLabel_(self, label: str) -> None:
        self._label = label
        self.setNeedsDisplay_(True)

    def isOpaque(self) -> bool:
        return False

    def drawRect_(self, _rect) -> None:
        bounds = self.bounds()
        dot_diameter = 8
        dot_y = (bounds.size.height - dot_diameter) / 2
        dot_rect = NSMakeRect(12, dot_y, dot_diameter, dot_diameter)
        AppKit.NSColor.systemRedColor().set()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                12, AppKit.NSFontWeightSemibold
            ),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        text = AppKit.NSString.stringWithString_(self._label)
        size = text.sizeWithAttributes_(attrs)
        text.drawAtPoint_withAttributes_((30, (bounds.size.height - size.height) / 2), attrs)


class MeetingIndicator:
    """Owns the panel and a 1Hz timer that updates the elapsed-time label. start() is idempotent
    (a no-op if already running); call it and stop() from the main thread only."""

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

        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, True, tick)
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
        elapsed = max(0, int(self._clock() - self._started_at)) if self._started_at else 0
        self._view.setLabel_(f"Recording {elapsed // 60}:{elapsed % 60:02d}")

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
