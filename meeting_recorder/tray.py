"""System tray icon (the notification area by the clock).

pystray runs its own message loop on a separate thread. Menu clicks arrive on that
thread, so every action is handed to the Tk thread through App.post().
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .app import App

ICON_PATH = Path(__file__).parent / "assets" / "icon.ico"


def _images():
    from PIL import Image, ImageDraw

    idle = Image.open(ICON_PATH).convert("RGBA").resize((64, 64))
    # While recording: a solid red dot with a white ring, readable at 16 px.
    rec = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(rec)
    d.ellipse([2, 2, 62, 62], fill=(235, 64, 52, 255))
    d.ellipse([22, 22, 42, 42], fill=(255, 255, 255, 255))
    return idle, rec


class Tray:
    def __init__(self, app: "App"):
        import pystray

        self.app = app
        self._pystray = pystray
        self._idle, self._rec = _images()

        def on_ui(fn: Callable[[], None]):
            return lambda icon, item: app.post(fn)

        menu = pystray.Menu(
            pystray.MenuItem("Open Meeting Recorder", on_ui(app.show_window), default=True),
            pystray.MenuItem(
                lambda item: "Stop recording" if app.recorder else "Start recording",
                on_ui(app.toggle_recording),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_ui(app.quit)),
        )
        self.icon = pystray.Icon("MeetingRecorder", self._idle, "Meeting Recorder", menu)

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_recording(self, recording: bool, title: str = "") -> None:
        self.icon.icon = self._rec if recording else self._idle
        self.icon.title = f"Recording: {title}"[:120] if recording else "Meeting Recorder"
        self.icon.update_menu()

    def notify(self, message: str, title: str = "Meeting Recorder") -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            pass  # notifications are a nicety; some shells don't support them


def create(app: "App") -> Tray | None:
    """Start the tray icon, or return None where there's no tray (the window then quits on close)."""
    try:
        tray = Tray(app)
        tray.start()
        return tray
    except Exception:
        return None
