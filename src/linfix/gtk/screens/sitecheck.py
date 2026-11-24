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
    "desync": _("Ломает desync"),
    "ip_block": _("Блокировка IP"),
    "dns": _("DNS"),
    "error": _("Ошибка"),
}


class SiteCheckScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Проверка сайта"))

        self.entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Без hexpand строка ввода тянется по содержимому: длинный домен
        # расширяет её шире окна, страница получает горизонтальную прокрутку,
        # и левый отступ «уезжает» вместе с контентом.
        self.entry_row.set_hexpand(True)
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
        self.exclude_button = common.button(
            _("Исключить из hostlist"), lambda _: self.exclude_from_hostlist()
        )
        self.add_button.set_sensitive(False)
        self.add_button.set_tooltip_text(
            _(
                "Активна, только если проверка показала «Блокировка SNI»: "
                "тогда zapret поможет. При блокировке по IP или TLS кнопка "
                "не нужна."
            )
        )
        self.exclude_button.set_tooltip_text(
            _(
                "Активна, если сайт уже в hostlist, но TLS ломается. "
                "Сначала лучше подобрать другую стратегию: удаление из "
                "hostlist оставит домен совсем без обхода."
            )
        )
        self.mtu_button = common.button(_("Починить MTU"), lambda _: self.fix_mtu())
        common.pack(self.buttons, self.add_button)
        common.pack(self.buttons, self.exclude_button)
        common.pack(self.buttons, self.mtu_button)

        common.pack(self.box, self.entry_row)
        common.pack(self.box, self.result)
        # Кнопки — до растягиваемого текста, иначе при нехватке высоты
        # GTK сдвигает их за нижний край окна.
        common.pack(self.box, self.buttons)
        common.pack_expand(self.box, common.scrolled(self.details))

        self._last: sitecheck.SiteCheck | None = None

    def on_shown(self) -> None:
        # MTU-кнопка всегда видима; обновляем только подпись.
        self._update_mtu_button()

    def _update_mtu_button(self) -> None:
        """Кнопка MTU полезна и при «медленно»: причину мог не увидеть тест."""

        result = self._last
        # Кнопка нужна при любом результате: MTU — самостоятельная настройка,
        # а не только реакция на вердикт.
        mtu = (result.suggested_mtu if result else None) or 1492
        self.mtu_button.set_label(_("Починить MTU (→{mtu})").format(mtu=mtu))
        self.mtu_button.set_visible(True)

    def fix_mtu(self) -> None:
        """Диалог исправления MTU: probing, авто-значение или ручной ввод."""

        result = self._last
        state = mtufix.read_state(result.ip if result else None)
        suggested = (result.suggested_mtu if result else None) or 1492
        current = state.interface_mtu or 1500

        dialog = Gtk.Dialog(title=_("MTU и PMTU"), transient_for=self.app.window)
        dialog.add_buttons(_("Отмена"), Gtk.ResponseType.CANCEL)
        dialog.set_default_size(520, 300)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        box.pack_start(
            common.label(_("Сейчас: {state}").format(state=mtufix.describe(state))), False, False, 0
        )
        box.pack_start(
            common.label(
                _(
                    "Меньший MTU помогает при «чёрной дыре» PMTU. "
                    "Значение можно задать вручную — например, 1480 или 1400."
                ),
                wrap=True,
            ),
            False,
            False,
            0,
        )

        mtu_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        common.pack(mtu_row, common.label(_("MTU:")))
        mtu_entry = common.entry()
        mtu_entry.set_text(str(suggested))
        mtu_entry.set_width_chars(6)
        common.pack(mtu_row, mtu_entry)
        if state.interface:
            common.pack(
                mtu_row,
                common.label(_("интерфейс: {iface}").format(iface=state.interface)),
            )
        box.pack_start(mtu_row, False, False, 0)

        probe_button = common.button(_("Включить tcp_mtu_probing"), lambda _: None)
        box.pack_start(probe_button, False, False, 0)

        hint = common.label(
            _("Диапазон 576…1500. Обычный MTU — 1500; для Wi-Fi/PPPoE часто 1492 или 1400."),
            wrap=True,
        )
        box.pack_start(hint, False, False, 0)

        apply_button = dialog.add_button(_("Применить MTU"), 2)
        restore_button = dialog.add_button(_("Вернуть 1500"), 3)
        # Возврат к стандартному MTU нужен, чтобы любой эксперимент был обратим.
        restore_button.set_sensitive(current < 1500)
        box.show_all()

        action = {"kind": None}

        def on_probe(_button) -> None:
            action["kind"] = "probe"
            dialog.response(1)

        probe_button.connect("clicked", on_probe)

        response = dialog.run()
        text = mtu_entry.get_text().strip()
        dialog.destroy()

        if action["kind"] == "probe":
            self._run_mtu_fix(lambda: mtufix.enable_probing(), "tcp_mtu_probing=1")
            return
        if response == 3:
            self._run_mtu_fix(
                lambda: mtufix.set_interface_mtu(state.interface, 1500, increase=True)
                if state.interface
                else (2, {"ok": False, "error": _("интерфейс не определён")}),
                "MTU → 1500",
            )
            return
        if response != 2:
            return
        if not state.interface:
            common.message(self.app, _("Не удалось определить сетевой интерфейс"))
            return
        try:
            value = int(text)
        except ValueError:
            common.message(self.app, _("MTU должен быть числом"))
            return
        if value == current:
            common.toast(self.app, _("MTU уже {mtu}").format(mtu=value))
            return
        self._run_mtu_fix(
            lambda: mtufix.set_interface_mtu(
                state.interface, value, increase=value > current
            ),
            _("MTU {iface} → {mtu}").format(iface=state.interface, mtu=value),
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
        self.check_button.set_label(_("⏳ Проверяется…"))
        self.result.set_text(_("⏳ Идёт проверка сайта…"))
        self.buffer.set_text("")
        self.add_button.set_sensitive(False)

        def job():
            return sitecheck.check_site(host)

        def done(result: sitecheck.SiteCheck) -> None:
            self.check_button.set_sensitive(True)
            self.check_button.set_label(_("Проверить"))
            self._last = result
            label = STATUS_LABELS.get(result.status, result.status)
            self.result.set_text(f"{label}: {result.summary}")
            lines = list(result.details)
            if result.ip:
                lines.append(f"{_('Адрес:')} {result.ip}")
            if result.advice:
                lines.append("")
                lines.append(f"{_('Что делать:')} {result.advice}")
            self.add_button.set_sensitive(result.status == "sni")
            self.exclude_button.set_sensitive(result.status == "desync")
            if result.status == "tls_ip":
                lines.append("")
                lines.append(
                    _(
                        "Добавлять в hostlist бесполезно: режется сам IP, "
                        "а не имя — zapret такой случай не обходит."
                    )
                )
            elif result.status == "ip_block":
                lines.append("")
                lines.append(
                    _(
                        "TCP не устанавливается: hostlist и zapret тут не "
                        "помогут, нужен прокси или VPN."
                    )
                )
            elif result.status == "ok":
                lines.append("")
                lines.append(_("Сайт открывается — добавлять в hostlist не нужно."))
            self.buffer.set_text("\n".join(lines))
            self._update_mtu_button()

        def failed(exc: Exception) -> None:
            self.check_button.set_sensitive(True)
            self.check_button.set_label(_("Проверить"))
            self.result.set_text(_("Ошибка проверки: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def exclude_from_hostlist(self) -> None:
        """Убрать домен из hostlist — если desync ломает именно его."""

        if self._last is None:
            return
        host = self._last.host

        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Исключить {host} из hostlist zapret?").format(host=host),
        )
        confirm.format_secondary_text(
            _(
                "zapret перестанет применять desync к этому домену. Если он есть "
                "в списке CF-доменов TgWsProxy, прокси может перестать работать "
                "через него. Служба будет перезапущена."
            )
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.exclude_button.set_sensitive(False)

        def job():
            return api.remove_from_hostlist(self.app.cfg, [host])

        def done(result) -> None:
            self.exclude_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            common.toast(
                self.app,
                _("Исключено: {n}").format(n=len(payload.get("removed") or [])),
            )
            self.run_check()

        def failed(exc: Exception) -> None:
            self.exclude_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

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
