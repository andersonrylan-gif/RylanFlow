"""Native window that shows the dashboard, backed by a WKWebView. Main thread only.

This module imports AppKit/WebKit at load time, so the caller is expected to import it lazily,
inside a try/except, and fall back to opening the dashboard in a regular browser if that fails
(e.g. off macOS, or a missing WebKit wheel).
"""

import logging

import AppKit
import WebKit
from Foundation import NSURL, NSMakeRect, NSURLRequest

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1000, 700


class _CloseDelegate(AppKit.NSObject):
    """Drops the Dock icon again once the dashboard window closes."""

    def windowWillClose_(self, _notification) -> None:
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


class DashboardWindow:
    """Lazily creates its NSWindow on the first show(); reuses it after that."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._window = None
        self._delegate = None  # kept alive here so AppKit doesn't release it early

    def show(self) -> None:
        if self._window is None:
            self._window = self._build()
        # LSUIElement apps can't bring a window to the front without this: without a Dock icon
        # (regular activation policy), macOS won't reliably give the window key focus.
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        self._window.center()
        self._window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def _build(self) -> AppKit.NSWindow:
        rect = NSMakeRect(0, 0, WIDTH, HEIGHT)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("RylanFlow")
        window.setMinSize_((640, 420))
        # Without this, closing the window would release and destroy it, so a second
        # "Open Dashboard" would have nothing to reopen.
        window.setReleasedWhenClosed_(False)

        self._delegate = _CloseDelegate.alloc().init()
        window.setDelegate_(self._delegate)

        webview = WebKit.WKWebView.alloc().initWithFrame_(rect)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self._url)))
        window.setContentView_(webview)
        return window
