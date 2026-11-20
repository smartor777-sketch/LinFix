"""Экран «Проверка сайта»: почему конкретный сайт не открывается."""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, sitecheck
from ..i18n import _
from ..worker import run_in_thread
from . import common

STATUS_LABELS = {
    "ok": _("Открывается"),
    "slow": _("Медленно"),
    "pmtu": _("Проблема MTU"),
    "sni": _("Блокировка SNI"),
    "tls_ip": _("Блокировка TLS/IP"),
    "http_only": _("Только HTTP"),
    "ip_block": _("Блокировка IP"),
    "dns": _("DNS"),
    "error": _("Ошибка"),
}


class SiteCheckScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Проверка сайта"))

        self.entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry = common.entry()
        self.entry.set_placeholder_text(_("например, rutracker.org"))
        self.entry.connect("activate", lambda *_: self.run_check())
        self.check_button = common.button(_("Проверить"), lambda _: self.run_check())
        common.pack_expand(self.entry_row, self.entry)
        common.pack(self.entry_row, self.check_button)

        self.result = common.label(_("Введите домен и нажмите «Проверить»"), wrap=True)
        self.details = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD)
        self.details.set_monospace(True)
        self.buffer = self.details.get_buffer()

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        self.add_button = common.button(_("Добавить в hostlist"), lambda _: self.add_to_hostlist())
        self.add_button.set_sensitive(False)
        common.pack(self.buttons, self.add_button)

        common.pack(self.box, self.entry_row)
        common.pack(self.box, self.result)
        common.pack_expand(self.box, common.scrolled(self.details))
        common.pack(self.box, self.buttons)

        self._last: sitecheck.SiteCheck | None = None

    def on_shown(self) -> None:
        pass

    def run_check(self) -> None:
        host = self.entry.get_text().strip()
        if not host:
            common.toast(self.app, _("Введите домен"))
            return

        self.check_button.set_sensitive(False)
        self.result.set_text(_("Проверяется…"))
        self.buffer.set_text("")
        self.add_button.set_sensitive(False)

        def job():
            return sitecheck.check_site(host)

        def done(result: sitecheck.SiteCheck) -> None:
            self.check_button.set_sensitive(True)
            self._last = result
            label = STATUS_LABELS.get(result.status, result.status)
            self.result.set_text(f"{label}: {result.summary}")
            lines = list(result.details)
            if result.ip:
                lines.append(f"{_('Адрес:')} {result.ip}")
            if result.advice:
                lines.append("")
                lines.append(f"{_('Что делать:')} {result.advice}")
            if result.status == "sni":
                self.add_button.set_sensitive(True)
            self.buffer.set_text("\n".join(lines))

        def failed(exc: Exception) -> None:
            self.check_button.set_sensitive(True)
            self.result.set_text(_("Ошибка проверки: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def add_to_hostlist(self) -> None:
        """Добавить проверенный домен в hostlist zapret (с рестартом)."""

        if self._last is None:
            return
        host = self._last.host
        self.add_button.set_sensitive(False)

        def job():
            return api.add_to_hostlist(self.app.cfg, [host])

        def done(result) -> None:
            self.add_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            common.toast(
                self.app,
                _("Добавлено в hostlist: {host}").format(host=host),
            )
            self.entry.set_text("")
            self._last = None

        def failed(exc: Exception) -> None:
            self.add_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)
