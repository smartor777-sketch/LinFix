"""Экран «Диагностика» (SPEC 14).

Вывод doctor в таблице + «Копировать отчёт», «Обновить».
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from ... import api
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

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        self.fix_button = common.button(_("Починить конфиг zapret"), lambda _: self.fix_config())
        # no_show_all: иначе show_all() окна покажет кнопку, когда она не нужна.
        self.fix_button.set_no_show_all(True)
        self.fix_button.set_visible(False)
        common.pack(self.buttons, self.fix_button)
        common.pack(self.buttons, common.button(_("Копировать отчёт"), lambda _: self.copy_report()))
        common.pack(self.buttons, common.button(_("Обновить"), lambda _: self.refresh()))

        # Кнопки — до растягиваемой таблицы, иначе уезжают за край окна.
        common.pack(self.box, self.buttons)
        common.pack_expand(self.box, self.scrolled)
        self.last_report = None
        self.refresh()

    def _update_fix_button(self, report) -> None:
        """Кнопка нужна только при находке «hostlist без --hostlist»."""

        broken = any(
            getattr(check, "id", "") == "zapret.hostlist_args" and check.status == "fail"
            for check in getattr(report, "checks", ())
        )
        self.fix_button.set_visible(broken)

    def fix_config(self) -> None:
        """Добавить --hostlist= перед голыми путями списков в config zapret."""

        self.fix_button.set_sensitive(False)

        def job():
            return api.fix_zapret_hostlist_args(self.app.cfg)

        def done(result) -> None:
            self.fix_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Не удалось исправить конфиг")))
                return
            if not payload.get("changed"):
                common.toast(self.app, _("Конфиг уже в порядке"))
            else:
                common.toast(
                    self.app,
                    _("Исправлено строк: {n}").format(n=len(payload.get("lines") or [])),
                )
            self.refresh()

        def failed(exc: Exception) -> None:
            self.fix_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def refresh(self) -> None:
        def job():
            return api.get_doctor_report(self.app.cfg)

        def apply_report(report):
            self.last_report = report
            self._update_fix_button(report)
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
