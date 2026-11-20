"""Экран «Статус» (SPEC 14).

Карточки: Zapret, TgWsProxy, Домены, Telegram + кнопки «Перезапустить»,
«Подключить Telegram».
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from ... import api, tgws, util, zapret
from ..i18n import _
from ..worker import run_in_thread
from . import common


class StatusScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Статус"))
        self.zapret_info = zapret.detect_zapret(app.cfg.zapret.service)
        self.has_zapret = self.zapret_info.found

        self.zapret_card = common.Card(_("Zapret"))
        for key, text in (
            ("service", _("Служба:")),
            ("active", _("Состояние:")),
            ("pid", _("PID:")),
            ("install_dir", _("Установка:")),
            ("qnum", _("Очередь (qnum):")),
            ("fwmark", _("fwmark:")),
            ("user", _("user:")),
            ("hostlist", _("Путь hostlist:")),
            ("cf", _("CF-домены:")),
        ):
            self.zapret_card.add(key, text)

        self.tgws_card = common.Card(_("TgWsProxy"))
        for key, text in (
            ("installed", _("Установлен:")),
            ("version", _("Версия:")),
            ("service", _("Служба:")),
            ("port", _("Порт:")),
        ):
            self.tgws_card.add(key, text)

        self.domains_card = common.Card(_("Домены"))
        for key, text in (
            ("present", _("В hostlist:")),
            ("total", _("Итого:")),
            ("updated", _("Обновлено:")),
        ):
            self.domains_card.add(key, text)

        self.telegram_card = common.Card(_("Telegram"))
        for key, text in (
            ("connections", _("Соединения:")),
            ("status", _("Статус:")),
            ("server", _("Сервер:")),
            ("port", _("Порт:")),
            ("secret", _("Секрет:")),
            ("link", _("Ссылка для Telegram")),
        ):
            self.telegram_card.add(key, text)

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=12)
        common.pack(self.box, self.zapret_card)
        common.pack(self.box, self.tgws_card)
        common.pack(self.box, self.domains_card)
        common.pack(self.box, self.telegram_card)
        common.pack(self.box, self.buttons)

        self.link_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=12)
        self.link_entry = common.entry()
        self.link_entry.set_editable(False)
        self.link_entry.set_placeholder_text(_("tg://proxy?server=127.0.0.1&port=1080&secret=dd…"))
        common.pack_expand(self.link_box, self.link_entry)
        common.pack(
            self.link_box,
            common.button(_("Копировать ссылку"), lambda _: self.copy_link()),
        )
        common.pack(self.link_box, common.button(_("QR"), lambda _: self.print_qr()))
        common.pack(self.box, self.link_box)

        self._build_buttons()
        self.refresh()

    def _build_buttons(self) -> None:
        common.pack(self.buttons, common.button(_("Перезапустить"), lambda _: self.app.run_op(api.restart_zapret, on_done=lambda _: self.refresh())))
        common.pack(self.buttons, common.button(_("Перезапустить TgWsProxy"), lambda _: self.app.run_op(api.restart_tgws, on_done=lambda _: self.refresh())))
        common.pack(self.buttons, common.button(_("Обновить"), lambda _: self.refresh()))
        common.pack(
            self.buttons,
            common.button(
                _("Остановить TgWsProxy"),
                lambda _: self.app.run_op(api.stop_tgws, on_done=lambda _: self.refresh()),
            ),
        )
        if self.has_zapret:
            common.pack(
                self.buttons,
                common.button(
                    _("Остановить Zapret2"),
                    lambda _: self.app.run_op(
                        lambda: api.stop_zapret(self.app.cfg),
                        on_done=lambda _: self.refresh(),
                    ),
                ),
            )
        common.pack(self.buttons, common.button(_("Подключить Telegram"), lambda _: self.connect_telegram()))

    def refresh(self) -> None:
        def job():
            status = api.get_status(self.app.cfg)
            info = status.zapret
            connections = len(zapret.listening_addresses(self.app.cfg.tgws.port))
            link = api.connect_telegram(self.app.cfg)
            telegram_settings = self._telegram_settings()
            return {
                "link": link or "",
                "server": telegram_settings["server"],
                "port": telegram_settings["port"],
                "secret": telegram_settings["secret"] or "—",
                "service": info.service or "—",
                "active": _("active") if info.active else _("inactive"),
                "pid": str(info.pid or "—"),
                "install_dir": info.install_dir or "—",
                "qnum": str(info.qnum or "—"),
                "fwmark": str(info.fwmark or "—"),
                "user": str(info.user or "—"),
                "hostlist": status.hostlist or "—",
                "cf": f"{status.cf_present}/{status.cf_total}",
                "tgws_installed": _("да") if status.tgws_installed else _("нет"),
                "tgws_version": status.state_info.tgws_version or "—",
                "tgws_service": tgws.service_state() if status.tgws_installed else "—",
                "tgws_port": ", ".join(status.listeners) or _("не слушается"),
                "domains_present": f"{len(status.state_info.domains or ())}/20",
                "domains_total": "20",
                "domains_updated": status.state_info.domains_updated_at or _("никогда"),
                "connections": str(connections),
                "telegram_status": _("active") if connections else _("inactive"),
            }

        def apply_data(data):
            self.zapret_card.set("service", data["service"])
            self.zapret_card.set("active", data["active"])
            self.zapret_card.set("pid", data["pid"])
            self.zapret_card.set("install_dir", data["install_dir"])
            self.zapret_card.set("qnum", data["qnum"])
            self.zapret_card.set("fwmark", data["fwmark"])
            self.zapret_card.set("user", data["user"])
            self.zapret_card.set("hostlist", data["hostlist"])
            self.zapret_card.set("cf", data["cf"])
            self.tgws_card.set("installed", data["tgws_installed"])
            self.tgws_card.set("version", data["tgws_version"])
            self.tgws_card.set("service", data["tgws_service"])
            self.tgws_card.set("port", data["tgws_port"])
            self.domains_card.set("present", data["domains_present"])
            self.domains_card.set("total", data["domains_total"])
            self.domains_card.set("updated", data["domains_updated"])
            self.telegram_card.set("connections", data["connections"])
            self.telegram_card.set("status", data["telegram_status"])
            self.telegram_card.set("server", data["server"])
            self.telegram_card.set("port", data["port"])
            self.telegram_card.set("secret", data["secret"])
            self.link_entry.set_text(data["link"] or "")

        run_in_thread(job, on_done=apply_data)

    def _telegram_settings(self) -> dict[str, str]:
        path = tgws.find_config_path()
        data = tgws.load_json_config(path) if path is not None else {}
        return {
            "server": str(data.get("host", "127.0.0.1")),
            "port": str(data.get("port", 1080)),
            "secret": str(data.get("secret", "")),
        }

    def copy_link(self) -> None:
        link = api.connect_telegram(self.app.cfg)
        if link:
            clipboard = Gtk.Clipboard.get_for_display(
                Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
            )
            clipboard.set_text(link, len(link))
            common.toast(self.app, _("Ссылка скопирована"))
        else:
            common.message(self.app, _("Конфиг TgWsProxy не найден"))

    def print_qr(self) -> None:
        link = api.connect_telegram(self.app.cfg)
        if not link:
            common.message(self.app, _("Конфиг TgWsProxy не найден"))
            return
        try:
            import qrcode
        except ImportError:
            common.message(self.app, _("Установите python3-qrcode для QR"))
            return
        code = qrcode.QRCode(border=2)
        code.add_data(link)
        code.make()
        code.print_ascii()

    def connect_telegram(self) -> None:
        link = api.connect_telegram(self.app.cfg)
        if link:
            clipboard = Gtk.Clipboard.get_for_display(
                Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
            )
            clipboard.set_text(link, len(link))
            common.toast(self.app, _("Ссылка скопирована"))
        else:
            common.message(self.app, _("Конфиг TgWsProxy не найден"))
