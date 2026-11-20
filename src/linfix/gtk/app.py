"""Главное GTK3-приложение LinFix (M6)."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, Gtk

from ..config import load_config
from . import icons
from .i18n import _, install
from .icons import tray_icon_path
from .tray import Tray
from .worker import run_in_thread
from .window import NetFixWindow


class NetFixApp(Gtk.Application):
    """`netfix-gui` — основное окно, трей, работа через `netfix.api`."""

    def __init__(self) -> None:
        super().__init__(application_id="org.linfix.gui", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.cfg = load_config()
        self.window: NetFixWindow | None = None
        self.tray: Tray | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = NetFixWindow(self)
        if self.tray is None:
            self.tray = Tray(self, tray_icon_path("ok"), tooltip="LinFix")
        self.window.present()

    def show(self) -> None:
        self.do_activate()

    def run_op(self, fn, on_done=None, **kwargs):
        """Запустить функцию в потоке, UI обновится через GLib.idle_add."""

        run_in_thread(fn, on_done=on_done, **kwargs)


def main() -> int:
    """Entry point: netfix-gui."""

    install(load_config().general.language)
    icon = icons.icon_path("linfix")
    if icon:
        Gtk.Window.set_default_icon_from_file(icon)
    app = NetFixApp()
    return app.run()
