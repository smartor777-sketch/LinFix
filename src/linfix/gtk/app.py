"""Главное GTK3-приложение LinFix (M6)."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, Gtk

from .. import api, tgws, util
from ..config import load_config
from . import icons
from .i18n import _, install
from .icons import tray_icon_path
from .tray import Tray
from .worker import run_in_thread
from .window import NetFixWindow


class NetFixApp(Gtk.Application):
    """`linfix-gui` — основное окно, трей, работа через `linfix.api`."""

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
        self.window.set_skip_taskbar_hint(False)
        self.start_autostarted_services()

    def start_autostarted_services(self) -> None:
        """При включённом автозапуске — запустить службы через systemd."""

        run_in_thread(self._autostarted_job)

    def _autostarted_job(self) -> None:
        cfg = self.cfg

        enabled = util.run(
            ["systemctl", "--user", "is-enabled", tgws.UNIT_NAME], check=False, timeout=10
        )
        if (enabled.stdout or "").strip() == "enabled":
            api.start_tgws(cfg)

        enabled_zapret2 = util.run(["systemctl", "is-enabled", "zapret2"], check=False, timeout=10)
        if (enabled_zapret2.stdout or "").strip() == "enabled":
            api.start_zapret(cfg)

    def show(self) -> None:
        self.do_activate()

    def _tray_restart(self, fn, status_key: str | None = None) -> None:
        def done(_result):
            if self.window is not None and status_key is not None:
                screen = self.window.screens.get(status_key)
                if screen is not None and hasattr(screen, "refresh"):
                    screen.refresh()

        self.run_op(fn, on_done=done)

    def restart_tgws_tray(self) -> None:
        self._tray_restart(api.restart_tgws, status_key="status")

    def restart_zapret_tray(self) -> None:
        self._tray_restart(api.restart_zapret, status_key="status")

    def run_op(self, fn, on_done=None, **kwargs):
        """Запустить функцию в потоке, UI обновится через GLib.idle_add."""

        run_in_thread(fn, on_done=on_done, **kwargs)


def main() -> int:
    """Entry point: linfix-gui."""

    install(load_config().general.language)
    icon = icons.icon_path("linfix")
    if icon:
        Gtk.Window.set_default_icon_from_file(icon)
    app = NetFixApp()
    return app.run()
