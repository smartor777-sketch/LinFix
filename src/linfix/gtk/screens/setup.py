"""Экран «Настройка» (мастер первой настройки, SPEC 14)."""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, doctor
from ..i18n import _
from . import common


class SetupScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Мастер настройки"))

        self.step_label = common.label(_("Проверка системы"), css_class="screen-title")
        self.desc = common.label(_("Описание шагов: 1 — система, 2 — TgWsProxy, 3 — домены, 4 — автозапуск"))

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=12)
        self.b_next = common.button(_("Далее"), lambda _: self.on_next())
        self.b_back = common.button(_("Назад"), lambda _: self.on_back())
        common.pack(self.buttons, self.b_next)
        common.pack(self.buttons, self.b_back)

        self.box.pack_start(self.step_label, False, False, 0)
        self.box.pack_start(self.desc, False, False, 0)
        self.box.pack_start(self.buttons, False, False, 0)

        self.steps = [
            (_("Проверка системы"), "", lambda: doctor.run_doctor(self.app.cfg)),
            (_("Установка TgWsProxy"), _("Скачать бинарник, проверить SHA256, установить"), lambda: api.install_tgws(self.app.cfg)),
            (_("Обновление доменов"), _("Скачать список CF-доменов и добавить в hostlist"), lambda: api.update_domains(self.app.cfg)),
            (_("Автозапуск"), _("Включить zapret2 и tgwsproxy, запустить сервисы"), lambda: api.enable_autostart(self.app.cfg, start=True)),
        ]
        self.idx = 0
        self._update_ui()

    def on_next(self) -> None:
        if self.idx >= len(self.steps):
            return
        title, _desc, action = self.steps[self.idx]
        self.idx += 1
        self._update_ui()
        if action:
            self.app.run_op(action)

    def on_back(self) -> None:
        if self.idx > 0:
            self.idx -= 1
        self._update_ui()

    def _update_ui(self) -> None:
        if self.idx >= len(self.steps):
            title = _("Готово")
            desc = _("Setup завершён")
            self.b_next.set_visible(False)
            self.b_next.set_label(_("Закрыть"))
            self.b_next.connect("clicked", lambda *_: self.app.quit())
        else:
            title, desc, _action = self.steps[self.idx]
            self.b_next.set_visible(True)
            self.b_next.set_label(_("Далее"))
        self.step_label.set_text(title)
        self.desc.set_text(desc)
        self.b_back.set_visible(self.idx > 0)
