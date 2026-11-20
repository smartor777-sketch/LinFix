"""Экран «Мастер настройки» (первичная настройка, SPEC 14).

Проходит шаги «как в Windows»: проверка → zapret2 → TgWsProxy → домены →
автозапуск. Привилегированные шаги запрашивают пароль через pkexec/sudo.
"""

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
        self.desc = common.label(
            _("Установит всё необходимое из репозиториев и настроит автозапуск")
        )

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=12)
        self.b_next = common.button(_("Далее"), lambda _: self.on_next())
        self.b_back = common.button(_("Назад"), lambda _: self.on_back())
        common.pack(self.buttons, self.b_next)
        common.pack(self.buttons, self.b_back)

        self.box.pack_start(self.step_label, False, False, 0)
        self.box.pack_start(self.desc, False, False, 0)
        self.box.pack_start(self.buttons, False, False, 0)

        self.steps = [
            (
                _("Проверка системы"),
                _("Проверим окружение, NFQUEUE, службы и домены"),
                lambda: doctor.run_doctor(self.app.cfg),
            ),
            (
                _("Установка Zapret2"),
                _("Скачать zapret2 из GitHub и запустить install_easy.sh (нужен пароль sudo)"),
                lambda: api.install_zapret2(self.app.cfg),
            ),
            (
                _("Установка TgWsProxy"),
                _("Скачать бинарник с GitHub, проверить SHA256 и установить (нужен пароль sudo)"),
                lambda: api.install_tgws(self.app.cfg),
            ),
            (
                _("Обновление доменов"),
                _("Скачать список CF-доменов и добавить в hostlist zapret"),
                lambda: api.update_domains(self.app.cfg),
            ),
            (
                _("Автозапуск"),
                _("Включить и сразу запустить zapret2 + TgWsProxy"),
                lambda: api.enable_autostart(self.app.cfg, start=True),
            ),
        ]
        self.idx = 0
        self._running = False
        self._update_ui()

    def on_next(self) -> None:
        if self._running:
            return
        if self.idx >= len(self.steps):
            if getattr(self, "_closing", False):
                # «Готово» — вернуться на Статус, а не закрывать приложение.
                self.idx = 0
                self._update_ui()
                self.app.window.set_page("status")
                status = self.app.window.screens.get("status")
                if status is not None and hasattr(status, "refresh"):
                    status.refresh()
                common.toast(self.app, _("Настройка завершена"))
            return
        _title, _desc, action = self.steps[self.idx]
        self._running = True
        self.b_next.set_sensitive(False)
        self.b_back.set_sensitive(False)
        self.step_label.set_text(_("Выполняется…"))
        if action:
            self.app.run_op(action, on_done=self._step_done, on_error=self._step_error)
        else:
            self._step_done(None)

    def on_back(self) -> None:
        if self._running or self.idx == 0:
            return
        self.idx -= 1
        self._update_ui()

    def _step_error(self, exc: Exception) -> None:
        self._running = False
        self.b_next.set_sensitive(True)
        self.b_back.set_sensitive(True)
        self._update_ui()
        common.toast(self.app, _("Ошибка: {detail}").format(detail=exc))

    def _step_done(self, result) -> None:
        ok, detail = True, ""
        if isinstance(result, tuple) and len(result) >= 2:
            code, payload = result[0], result[1]
            ok = code == 0
            if isinstance(payload, dict):
                ok = ok and payload.get("ok", True) is not False
                detail = str(payload.get("error") or "")
            else:
                detail = str(payload or "")
        self._running = False
        self.b_next.set_sensitive(True)
        self.b_back.set_sensitive(True)
        if ok:
            # Шаг выполнен — переходим к следующему.
            self.idx += 1
            self._update_ui()
            common.toast(self.app, _("Готово"))
        else:
            # Остаёмся на шаге: можно повторить или уйти «Назад».
            self._update_ui()
            common.toast(self.app, _("Ошибка: {detail}").format(detail=detail or _("см. Логи")))

    def _update_ui(self) -> None:
        if self.idx >= len(self.steps):
            title = _("Готово")
            desc = _("Настройка завершена")
            self.b_next.set_visible(True)
            self.b_next.set_label(_("Готово"))
            self.b_back.set_visible(False)
            self._closing = True
        else:
            self._closing = False
            title, desc, _action = self.steps[self.idx]
            self.b_next.set_visible(True)
            self.b_next.set_label(_("Далее"))
            self.b_back.set_visible(self.idx > 0)
        self.step_label.set_text(title)
        self.desc.set_text(desc)
