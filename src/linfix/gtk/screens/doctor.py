"""Экран «Диагностика» (SPEC 14).

Вывод doctor в таблице + «Копировать отчёт», «Обновить».
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from ... import api, netdiag, tgws, zapret
from ..i18n import _
from ..worker import run_in_thread
from . import common


class DoctorScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Диагностика"))

        self.store = Gtk.ListStore(str, str, str, str)
        self.tree = common.table_from_store(
            self.store,
            [_("Проверка"), _("Статус"), _("Детали"), _("Подсказка")],
        )
        self.scrolled = common.scrolled(self.tree)

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        self.net_button = common.button(
            _("Сетевая диагностика"), lambda _: self.run_network_diag()
        )
        common.pack(self.buttons, self.net_button)
        common.pack(self.buttons, common.button(_("Копировать отчёт"), lambda _: self.copy_report()))
        common.pack(self.buttons, common.button(_("Обновить"), lambda _: self.refresh()))

        # Вердикт + рекомендации + сетевые проверки (по мотивам NetFix).
        self.verdict_label = common.label("", wrap=True, css_class="screen-title")
        self.net_store = Gtk.ListStore(str, str, str)
        self.net_tree = common.table_from_store(
            self.net_store,
            [_("Проверка"), _("Статус"), _("Детали")],
        )
        self.net_scrolled = common.scrolled(self.net_tree)
        self.net_scrolled.set_min_content_height(180)
        self.reco_label = common.label("", wrap=True)

        # Профили desync — только просмотр: какие правила реально работают.
        self.profile_store = Gtk.ListStore(str, str, str, str)
        self.profile_tree = common.table_from_store(
            self.profile_store,
            [_("Протокол"), _("Статус"), _("Действует на"), _("Стратегия")],
        )
        # Высоту задаём самому ScrolledWindow: size_request на TreeView внутри
        # него не влияет — окно прокрутки всё равно просит минимум и
        # показывает две строки.
        self.profile_scrolled = common.scrolled(self.profile_tree)
        self.profile_scrolled.set_min_content_height(200)
        self.profile_note = common.label("", wrap=True)

        common.pack(self.box, self.buttons)
        common.pack(self.box, self.verdict_label)
        common.pack(self.box, self.net_scrolled)
        common.pack(self.box, self.reco_label)
        common.pack(self.box, self.profile_note)
        common.pack(self.box, self.profile_scrolled)
        common.pack_expand(self.box, self.scrolled)
        self.last_report = None
        self.refresh()
        self.refresh_profiles()

    def run_network_diag(self) -> None:
        """Проверить сеть до Telegram: DC, DNS, DPI, медиа — с вердиктом."""

        self.net_button.set_sensitive(False)
        self.verdict_label.set_text(_("Проверяется… это занимает до 20 секунд"))

        def job():
            return netdiag.run_network_diagnostics(
                proxy_running=bool(tgws.service_state() == "active"),
                zapret_running=bool(zapret.detect_zapret(self.app.cfg.zapret.service).active),
                timeout=2.5,
            )

        def done(report) -> None:
            self.net_button.set_sensitive(True)
            emoji, title, detail = report.verdict
            self.verdict_label.set_text(f"{emoji} {title}\n{detail}")
            self.net_store.clear()
            for title_row, status, details in report.as_rows():
                self.net_store.append(
                    [title_row, common.status_label(status).get_text(), details]
                )
            self.reco_label.set_text(
                _("Что делать:\n") + "\n".join(f"• {tip}" for tip in report.recommendations)
            )

        def failed(exc: Exception) -> None:
            self.net_button.set_sensitive(True)
            self.verdict_label.set_text(_("Ошибка диагностики: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def refresh_profiles(self) -> None:
        """Показать профили zapret: активные и игнорируемые."""

        def job():
            return zapret.read_profiles()

        def done(profiles) -> None:
            self.profile_store.clear()
            if not profiles:
                self.profile_note.set_text(_("Профили zapret не найдены (конфиг недоступен)"))
                return
            ignored = 0
            for profile in profiles:
                active = bool(profile.get("active"))
                if not active:
                    ignored += 1
                strategy = ", ".join(profile.get("desync") or [])[:60]
                self.profile_store.append(
                    [
                        str(profile.get("protocol") or "—"),
                        _("работает") if active else _("игнорируется"),
                        str(profile.get("applies_to") or "—"),
                        strategy,
                    ]
                )
            note = _(
                "Профили desync: {n} правил, из них игнорируется {ignored}. "
                "«Игнорируется» — путь к списку записан без --hostlist=: nfqws2 "
                "такую строку не применяет. Это безопасно (правило не работает) "
                "и ничего менять не нужно."
            ).format(n=len(profiles), ignored=ignored)
            self.profile_note.set_text(note)

        run_in_thread(job, on_done=done)

    def refresh(self) -> None:
        def job():
            return api.get_doctor_report(self.app.cfg)

        def apply_report(report):
            self.last_report = report
            self.store.clear()
            for check in report.checks:
                self.store.append(
                    [
                        check.title,
                        common.status_label(check.status).get_text(),
                        check.details,
                        check.hint,
                    ]
                )

        run_in_thread(job, on_done=apply_report)

    def copy_report(self) -> None:
        if self.last_report is None:
            common.toast(self.app, _("Отчёт ещё не создан"))
            return
        clipboard = Gtk.Clipboard.get_for_display(
            Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
        )
        clipboard.set_text(self.last_report.to_json(), len(self.last_report.to_json()))
        common.toast(self.app, _("Отчёт скопирован"))
