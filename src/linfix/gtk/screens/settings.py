"""Экран «Настройки» (SPEC 14).

TgWsProxy config: порт, хост, verbose, guard, автозапуск.
"""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, tgws, util
from ..i18n import _
from . import common

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class SettingsScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Настройки"))

        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(8)
        self.grid.set_row_spacing(6)

        self.port_entry = common.entry()
        self.host_entry = common.entry()
        self.verbose_switch = common.switch(False)
        self.guard_switch = common.switch(False)
        self.autostart_switch = common.switch(False)

        def attach(row: int, widget: Gtk.Widget, label_text: str) -> None:
            self.grid.attach(Gtk.Label(label=label_text, halign=Gtk.Align.START), 0, row, 1, 1)
            self.grid.attach(widget, 1, row, 1, 1)

        attach(0, self.port_entry, _("Порт:"))
        attach(1, self.host_entry, _("Хост:"))
        attach(2, self.verbose_switch, _("Логирование verbose"))
        attach(3, self.guard_switch, _("Guard"))
        attach(4, self.autostart_switch, _("Автозапуск TgWsProxy"))

        self.box.pack_start(self.grid, False, False, 0)
        self.save_button = common.button(_("Сохранить"), lambda _: self.save())
        common.pack(self.box, self.save_button)

        self.load()

    def load(self) -> None:
        path = tgws.find_config_path()
        data = tgws.load_json_config(path) if path is not None else {}
        self.port_entry.set_text(str(data.get("port", 1080)))
        self.host_entry.set_text(str(data.get("host", "127.0.0.1")))
        self.verbose_switch.set_active(bool(data.get("verbose", False)))
        self.guard_switch.set_active(bool(self.app.cfg.guard.enabled))
        completed = util.run(["systemctl", "--user", "is-enabled", tgws.UNIT_NAME], check=False, timeout=10)
        self.autostart_switch.set_active((completed.stdout or "").strip() == "enabled")

    def save(self) -> None:
        changes: dict[str, object] = {}
        try:
            changes["port"] = int(self.port_entry.get_text() or "1080")
        except ValueError:
            common.message(self.app, _("Некорректный порт"))
            return
        host = self.host_entry.get_text() or "127.0.0.1"
        if host not in LOOPBACK:
            common.message(self.app, _("В MVP разрешён только loopback: 127.0.0.1, localhost, ::1"))
            return
        changes["host"] = host
        changes["verbose"] = self.verbose_switch.get_active()

        code, payload = api.set_tgws_config(changes)
        if payload.get("ok") is False:
            common.message(self.app, str(payload.get("error", _("Ошибка"))))
            return

        api.toggle_guard(self.app.cfg, self.guard_switch.get_active())
        api.enable_autostart(self.app.cfg, start=False)
        common.toast(self.app, _("Настройки сохранены"))
