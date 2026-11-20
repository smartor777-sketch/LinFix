"""Экран «Настройки» (SPEC 14).

TgWsProxy config: порт, хост, verbose, guard, автозапуск;
кнопки управления службами и Telegram-ссылка (перенесены из «Статус»).
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

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
        self.zapret2_switch = common.switch(False)

        def attach(row: int, widget: Gtk.Widget, label_text: str) -> None:
            self.grid.attach(Gtk.Label(label=label_text, halign=Gtk.Align.START), 0, row, 1, 1)
            self.grid.attach(widget, 1, row, 1, 1)

        attach(0, self.port_entry, _("Порт:"))
        attach(1, self.host_entry, _("Хост:"))
        attach(2, self.verbose_switch, _("Логирование verbose"))
        attach(3, self.guard_switch, _("Guard"))
        attach(4, self.autostart_switch, _("Автозапуск TgWsProxy"))
        attach(5, self.zapret2_switch, _("Автозапуск Zapret2"))

        self.box.pack_start(self.grid, False, False, 0)

        # Кнопки управления службами (перенесены из «Статус»).
        self.services_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        common.pack(
            self.services_box,
            common.button(_("Перезапустить"), lambda _: self._run_op(lambda: api.restart_zapret(self.app.cfg))),
        )
        common.pack(
            self.services_box,
            common.button(_("Перезапустить TgWsProxy"), lambda _: self._run_op(lambda: api.restart_tgws(self.app.cfg))),
        )
        common.pack(self.services_box, common.button(_("Остановить TgWsProxy"), lambda _: self._run_op(api.stop_tgws)))
        common.pack(
            self.services_box,
            common.button(_("Остановить Zapret2"), lambda _: self._run_op(lambda: api.stop_zapret(self.app.cfg))),
        )
        common.pack(self.services_box, common.button(_("Подключить Telegram"), lambda _: self.open_telegram()))
        common.pack(self.box, self.services_box)

        # Telegram: ссылка (перенесена из «Статус»).
        self.link_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=12)
        self.link_entry = common.entry()
        self.link_entry.set_editable(False)
        self.link_entry.set_placeholder_text(_("tg://proxy?server=127.0.0.1&port=1080&secret=dd…"))
        common.pack_expand(self.link_box, self.link_entry)
        common.pack(self.link_box, common.button(_("Копировать ссылку"), lambda _: self.copy_link()))
        common.pack(self.box, self.link_box)

        self.save_button = common.button(_("Сохранить"), lambda _: self.save())
        common.pack(self.box, self.save_button)

        self.load()
        self._refresh_link()

    def _run_op(self, fn) -> None:
        self.app.run_op(fn, on_done=lambda _: self._refresh_link())

    def _refresh_link(self) -> None:
        self.link_entry.set_text(api.connect_telegram(self.app.cfg) or "")

    def copy_link(self) -> None:
        link = api.connect_telegram(self.app.cfg)
        if not link:
            common.message(self.app, _("Конфиг TgWsProxy не найден"))
            return
        clipboard = Gtk.Clipboard.get_for_display(Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(link, len(link))
        common.toast(self.app, _("Ссылка скопирована"))

    def open_telegram(self) -> None:
        link = api.connect_telegram(self.app.cfg)
        if not link:
            common.message(self.app, _("Конфиг TgWsProxy не найден"))
            return
        try:
            Gtk.show_uri(Gdk.Screen.get_default(), link, Gdk.CURRENT_TIME)
            common.toast(self.app, _("Открыта ссылка для настройки Telegram"))
        except Exception:
            self.copy_link()

    def load(self) -> None:
        path = tgws.find_config_path()
        data = tgws.load_json_config(path) if path is not None else {}
        self.port_entry.set_text(str(data.get("port", 1080)))
        self.host_entry.set_text(str(data.get("host", "127.0.0.1")))
        self.verbose_switch.set_active(bool(data.get("verbose", False)))
        self.guard_switch.set_active(bool(self.app.cfg.guard.enabled))
        completed = util.run(["systemctl", "--user", "is-enabled", tgws.UNIT_NAME], check=False, timeout=10)
        self.autostart_switch.set_active((completed.stdout or "").strip() == "enabled")
        completed_zapret2 = util.run(["systemctl", "is-enabled", "zapret2"], check=False, timeout=10)
        self.zapret2_switch.set_active((completed_zapret2.stdout or "").strip() == "enabled")

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

        completed = util.run(["systemctl", "is-enabled", "zapret2"], check=False, timeout=10)
        zapret2_enabled = (completed.stdout or "").strip() == "enabled"
        if self.zapret2_switch.get_active() and not zapret2_enabled:
            code, payload = api.set_zapret2_autostart(True)
            if not payload.get("ok"):
                common.message(self.app, str(payload.get("error", _("Ошибка"))))
        elif not self.zapret2_switch.get_active() and zapret2_enabled:
            code, payload = api.set_zapret2_autostart(False)
            if not payload.get("ok"):
                common.message(self.app, str(payload.get("error", _("Ошибка"))))

        # Автозапуск включён — поднимаем службы через systemd.
        if self.autostart_switch.get_active():
            api.start_tgws(self.app.cfg)
        if self.zapret2_switch.get_active():
            api.start_zapret(self.app.cfg)

        common.toast(self.app, _("Настройки сохранены"))
