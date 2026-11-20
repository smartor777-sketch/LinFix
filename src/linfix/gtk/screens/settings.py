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
        self.log_max_entry = common.entry()
        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.append(tgws.MODE_TRAY, _("Трей (нужен X11)"))
        self.mode_combo.append(tgws.MODE_CLI, _("CLI (без графики)"))
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
        attach(3, self.log_max_entry, _("Размер лога, МБ:"))
        attach(4, self.mode_combo, _("Режим TgWsProxy:"))
        attach(5, self.guard_switch, _("Guard"))
        attach(6, self.autostart_switch, _("Автозапуск TgWsProxy"))
        attach(7, self.zapret2_switch, _("Автозапуск Zapret2"))

        self.box.pack_start(self.grid, False, False, 0)

        # Кнопки управления службами (перенесены из «Статус»).
        # FlowBox переносит кнопки на новую строку, если окно узкое —
        # иначе ряд шире окна и правые кнопки уходят за край.
        self.services_box = Gtk.FlowBox()
        self.services_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.services_box.set_max_children_per_line(10)
        self.services_box.set_min_children_per_line(1)
        self.services_box.set_homogeneous(False)
        self.services_box.set_column_spacing(6)
        self.services_box.set_row_spacing(6)
        self.services_box.set_margin_top(8)
        self.services_box.set_halign(Gtk.Align.FILL)
        self.services_box.set_hexpand(True)

        def add_service(text: str, handler) -> None:
            self.services_box.add(common.button(text, handler))

        add_service(_("Перезапустить"), lambda _: self._run_op(lambda: api.restart_zapret(self.app.cfg)))
        add_service(
            _("Перезапустить TgWsProxy"), lambda _: self._run_op(lambda: api.restart_tgws(self.app.cfg))
        )
        add_service(_("Остановить TgWsProxy"), lambda _: self._run_op(api.stop_tgws))
        add_service(
            _("Остановить Zapret2"), lambda _: self._run_op(lambda: api.stop_zapret(self.app.cfg))
        )
        add_service(_("Подключить Telegram"), lambda _: self.open_telegram())
        add_service(_("Очистить логи"), lambda _: self.clear_logs())
        # fill=True по горизонтали в вертикальном Box — на всю ширину окна.
        self.box.pack_start(self.services_box, False, True, 0)

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

    def on_shown(self) -> None:
        """При входе на вкладку подтянуть актуальный конфиг (мог измениться из CLI)."""
        self.load()
        self._refresh_link()

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

    def clear_logs(self) -> None:
        """Удалить proxy.log и его ротированные бэкапы (TgWsProxy)."""

        removed: list[str] = []
        candidates = list(tgws.log_candidates())
        path = tgws.find_log_path()
        if path is not None:
            candidates.append(path)
        for base in candidates:
            for target in sorted({base, base.with_suffix(base.suffix + ".1")}):
                try:
                    if target.exists():
                        target.unlink()
                        removed.append(target.name)
                except OSError as exc:
                    common.message(self.app, _("Не удалось удалить {name}: {err}").format(name=target.name, err=exc))
                    return
        if removed:
            common.toast(self.app, _("Удалено: {names}").format(names=", ".join(removed)))
        else:
            common.toast(self.app, _("Логи уже пусты"))

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
        self.log_max_entry.set_text(str(data.get("log_max_mb", 5)))
        mode = data.get("linfix_mode")
        if mode not in tgws.MODES:
            mode = tgws.MODE_TRAY
        self.mode_combo.set_active_id(str(mode))
        self._saved_mode = str(mode)
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
        try:
            changes["log_max_mb"] = float(self.log_max_entry.get_text() or "5")
        except ValueError:
            common.message(self.app, _("Некорректный размер лога"))
            return

        code, payload = api.set_tgws_config(changes)
        if payload.get("ok") is False:
            common.message(self.app, str(payload.get("error", _("Ошибка"))))
            return

        mode = self.mode_combo.get_active_id()
        previous_mode = self._saved_mode
        if mode in tgws.MODES and mode != previous_mode:
            code, mode_payload = api.set_tgws_mode(self.app.cfg, str(mode))
            if mode_payload.get("ok") is False:
                common.message(self.app, str(mode_payload.get("error", _("Ошибка"))))
                return
            self._saved_mode = str(mode)

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
