"""Экран «Проверка сайта»: почему конкретный сайт не открывается."""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, mtufix, sitecheck
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
        self.mtu_button = common.button(_("Починить MTU"), lambda _: self.fix_mtu())
        # no_show_all: иначе show_all() окна снова покажет кнопку.
        self.mtu_button.set_no_show_all(True)
        self.mtu_button.set_visible(False)
        common.pack(self.buttons, self.add_button)
        common.pack(self.buttons, self.mtu_button)

        common.pack(self.box, self.entry_row)
        common.pack(self.box, self.result)
        # Кнопки — до растягиваемого текста, иначе при нехватке высоты
        # GTK сдвигает их за нижний край окна.
        common.pack(self.box, self.buttons)
        common.pack_expand(self.box, common.scrolled(self.details))

        self._last: sitecheck.SiteCheck | None = None

    def on_shown(self) -> None:
        self._update_mtu_button()

    def _update_mtu_button(self) -> None:
        """Кнопка MTU нужна только тогда, когда проблема именно в PMTU."""

        result = self._last
        if result is not None and result.status == "pmtu":
            mtu = result.suggested_mtu or 1492
            self.mtu_button.set_label(_("Починить MTU (→{mtu})").format(mtu=mtu))
            self.mtu_button.set_visible(True)
        else:
            self.mtu_button.set_visible(False)

    def fix_mtu(self) -> None:
        """Исправить «чёрную дыру» PMTU: probing или MTU интерфейса."""

        result = self._last
        if result is None or result.status != "pmtu":
            return
        target_mtu = result.suggested_mtu or 1492
        state = mtufix.read_state(result.ip)

        dialog = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Как исправить MTU?"),
        )
        dialog.format_secondary_text(
            _(
                "Сейчас: {state}.\n\n"
                "1) tcp_mtu_probing — ядро само подберёт MTU (мягко, обратимо, "
                "но помогает не всегда).\n"
                "2) Уменьшить MTU интерфейса до {mtu} — действует сразу; сбросится "
                "при переподключении Wi-Fi, если не закрепить в NetworkManager."
            ).format(state=mtufix.describe(state), mtu=target_mtu)
        )
        probe_button = dialog.add_button(_("Включить tcp_mtu_probing"), 1)
        set_button = dialog.add_button(_("Уменьшить MTU до {mtu}").format(mtu=target_mtu), 2)
        if state.interface is None:
            set_button.set_sensitive(False)
        dialog.add_button(_("Отмена"), Gtk.ResponseType.CANCEL)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()

        if response == 1:
            self._run_mtu_fix(lambda: mtufix.enable_probing(), _("tcp_mtu_probing=1"))
        elif response == 2 and state.interface:
            self._run_mtu_fix(
                lambda: mtufix.set_interface_mtu(state.interface, target_mtu),
                _("MTU {iface} → {mtu}").format(iface=state.interface, mtu=target_mtu),
            )

    def _run_mtu_fix(self, fn, label: str) -> None:
        self.mtu_button.set_sensitive(False)

        def job():
            return fn()

        def done(result) -> None:
            self.mtu_button.set_sensitive(True)
            code, payload = result
            if code != 0 or not payload.get("ok"):
                common.message(
                    self.app, str(payload.get("error") or _("Не удалось изменить MTU"))
                )
                return
            common.toast(self.app, _("Готово: {label}").format(label=label))
            self.run_check()

        def failed(exc: Exception) -> None:
            self.mtu_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

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
            self._update_mtu_button()

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
