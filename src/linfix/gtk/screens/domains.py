"""Экран «Домены»: CF-домены TgWsProxy (список для прокси, не для zapret).

Сверху — пояснение и кнопки, ниже — таблица с галочками. Тест TLS и удаление
применяются к отмеченным доменам; «весь список» — отдельной кнопкой.
"""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, cfdomains, cfprofile
from ..i18n import _
from ..worker import run_in_thread
from . import common


class DomainsScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Домены"))

        self.info = common.label(_("—"), wrap=True)
        self.note = common.label(
            _(
                "Список ниже — CF-домены из GitHub для hostlist zapret, а не "
                "текущая ротация TgWsProxy. Все домены должны быть в hostlist "
                "zapret — иначе прокси ходит через заблокированный SNI."
            ),
            wrap=True,
        )

        # Источник ротации показываем отдельно: это конфиг TgWsProxy,
        # а не список доменов для hostlist zapret.
        self.rotation_label = common.label(_("—"), wrap=True)
        self.rotation_combo = Gtk.ComboBoxText()
        self.rotation_combo.append(api.ROTATION_USER, _("Пользовательские домены"))
        self.rotation_combo.append(api.ROTATION_GITHUB, _("Домены с GitHub (авто)"))
        self.rotation_apply = common.button(_("Применить"), self.apply_rotation)
        rotation_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=4)
        common.pack(rotation_row, self.rotation_combo)
        common.pack(rotation_row, self.rotation_apply)
        self._rotation_mode: str | None = None

        # Кнопки — до списка, как привычнее: действие → результат.
        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=4)
        self.refresh_button = common.button(_("Обновить и проверить"), lambda _: self.run_update())
        self.test_button = common.button(_("Тест TLS выбранных"), lambda _: self.run_test())
        self.test_all_button = common.button(_("Тест всего списка"), lambda _: self.run_test(all_domains=True))
        self.add_button = common.button(_("Добавить домен"), lambda _: self.add_domain())
        self.remove_button = common.button(_("Удалить выбранные"), lambda _: self.remove_selected())
        self.profile_button = common.button(
            _("Проверить профиль CF"), lambda _: self.run_profile_check()
        )
        for button in (
            self.refresh_button,
            self.test_button,
            self.test_all_button,
            self.profile_button,
            self.add_button,
            self.remove_button,
        ):
            common.pack(self.buttons, button)

        # Галочки: простой и привычный выбор нескольких строк.
        self.store = Gtk.ListStore(bool, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        toggle = Gtk.CellRendererToggle()
        toggle.set_property("activatable", True)
        toggle.connect("toggled", self._on_toggled)
        self.tree.append_column(Gtk.TreeViewColumn("", toggle, active=0))
        for index, title in enumerate((_("Домен"), _("Статус"), _("Детали")), start=1):
            renderer = Gtk.CellRendererText()
            self.tree.append_column(Gtk.TreeViewColumn(title, renderer, text=index))
        self.store.set_sort_column_id(1, Gtk.SortType.ASCENDING)
        self.scrolled = common.scrolled(self.tree)

        common.pack(self.box, self.rotation_label)
        common.pack(self.box, rotation_row)
        common.pack(self.box, self.info)
        common.pack(self.box, self.note)
        common.pack(self.box, self.buttons)
        common.pack_expand(self.box, self.scrolled)

        self._domains: list[str] = []
        self.refresh()

    # ------------------------------------------------------------------

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        def job():
            return api.get_cf_domains(self.app.cfg), api.get_tgws_rotation()

        def done(result) -> None:
            cf_result, rotation_result = result
            self._update_rotation(rotation_result)
            code, payload = cf_result
            if code != api.EXIT_OK or payload.get("ok") is False:
                self.info.set_text(str(payload.get("error") or _("Не удалось прочитать список")))
                return
            self._domains = list(payload.get("domains") or [])
            updated = payload.get("updated_at") or _("никогда")
            missing = payload.get("missing") or []
            self.info.set_text(
                _("Доменов: {n}, в hostlist zapret: {m}, обновлено: {when}").format(
                    n=len(self._domains),
                    m=payload.get("in_hostlist", 0),
                    when=updated,
                )
                + (
                    _(" — нет в hostlist: {n}").format(n=len(missing)) if missing else ""
                )
            )
            previous = {row[1] for row in self.store if row[0]}
            self.store.clear()
            for domain in self._domains:
                status = _("MISSING") if domain in missing else _("OK")
                self.store.append([domain in previous, domain, status, ""])

        run_in_thread(job, on_done=done)

    def _update_rotation(self, result) -> None:
        code, payload = result
        if code != api.EXIT_OK or payload.get("ok") is False:
            self.rotation_label.set_text(
                str(payload.get("error") or _("Не удалось прочитать ротацию"))
            )
            return
        mode = str(payload.get("mode") or api.ROTATION_GITHUB)
        domains = [str(item) for item in payload.get("domains", [])]
        self._rotation_mode = mode
        self.rotation_combo.set_active_id(mode)
        shown = ", ".join(domains[:4]) + (" …" if len(domains) > 4 else "")
        if mode == api.ROTATION_USER:
            text = _("Ротация TgWsProxy: пользовательские домены ({n}): {list}. Автообновление с GitHub выключено.").format(
                n=len(domains), list=shown or "—"
            )
        else:
            text = _("Ротация TgWsProxy: домены с GitHub ({n}, автообновление включено): {list}").format(
                n=len(domains), list=shown or "—"
            )
        self.rotation_label.set_text(text)

    def apply_rotation(self, _button=None) -> None:
        target = self.rotation_combo.get_active_id()
        if not target or target == self._rotation_mode:
            return
        if target == api.ROTATION_GITHUB:
            question = _("Переключить ротацию на домены с GitHub?")
            detail = _(
                "TgWsProxy начнёт брать домены из списка GitHub и включит "
                "автообновление.\n\n"
                "Важно: текущий список GitHub содержит .com-домены, которые "
                "уже не резолвятся. Скорее всего, медиа и часть трафика "
                "перестанут работать.\n\n"
                "Список пользовательских доменов сохранится — переключиться "
                "обратно можно тем же выпадающим списком."
            )
        else:
            question = _("Переключить ротацию на пользовательские домены?")
            detail = _(
                "Список GitHub перестанет обновляться; вернутся сохранённые "
                "пользовательские домены."
            )
        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=question,
        )
        confirm.format_secondary_text(detail)
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            self.rotation_combo.set_active_id(self._rotation_mode)
            return

        self.rotation_apply.set_sensitive(False)

        def job():
            return api.set_tgws_rotation(self.app.cfg, str(target))

        def done(result) -> None:
            self.rotation_apply.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
            else:
                common.toast(
                    self.app,
                    _("Ротация переключена: {mode}").format(
                        mode=_("пользовательские домены")
                        if target == api.ROTATION_USER
                        else _("домены с GitHub")
                    ),
                )
                if not payload.get("restarted"):
                    common.message(
                        self.app,
                        _("Настройки сохранены, но служба не перезапустилась — перезапустите TgWsProxy вручную"),
                    )
            self.refresh()

        def failed(exc: Exception) -> None:
            self.rotation_apply.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def _on_toggled(self, _renderer, path: str) -> None:
        self.store[path][0] = not self.store[path][0]

    def _selected(self) -> list[str]:
        return [row[1] for row in self.store if row[0]]

    # ------------------------------------------------------------------

    def run_update(self) -> None:
        self.refresh_button.set_sensitive(False)

        def job():
            return api.update_domains(self.app.cfg, force=True)

        def done(result) -> None:
            self.refresh_button.set_sensitive(True)
            code, payload = result
            if payload.get("ok") is False:
                common.message(self.app, str(payload.get("error", _("Ошибка"))))
            else:
                common.toast(
                    self.app,
                    _("Обновлено: {n} доменов").format(n=len(payload.get("missing_before") or [])),
                )
            self.refresh()

        def failed(exc: Exception) -> None:
            self.refresh_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def run_profile_check(self) -> None:
        """Проверить, что профиль zapret пропускает большие H2-тела.

        Обычный TLS-тест этого не видит: `tcpseg` открывает домен, но ломает
        передачу медиа через HTTP/2. Здесь проверяется POST на 256 КБ.
        """

        selected = self._selected() or list(self._domains[: cfprofile.DEFAULT_LIMIT])
        if not selected:
            common.message(self.app, _("Нет доменов для проверки"))
            return

        self.profile_button.set_sensitive(False)

        def job():
            return cfprofile.check_profile(selected)

        def done(report) -> None:
            self.profile_button.set_sensitive(True)
            details = {probe.domain: probe for probe in report.probes}
            for row in self.store:
                probe = details.get(row[1])
                if probe is None:
                    continue
                if not probe.tls_ok:
                    row[2] = _("FAIL")
                    row[3] = _("TLS: {err}").format(err=probe.h2_error or "ошибка")
                elif probe.body_ok:
                    row[2] = _("OK")
                    row[3] = _("H2 {kb} КБ за {ms} мс").format(
                        kb=cfprofile.BODY_BYTES // 1024, ms=probe.h2_ms
                    )
                else:
                    row[2] = _("FAIL")
                    row[3] = _("H2: {err}").format(err=probe.h2_error or "таймаут")
            text = report.detection
            if report.hint:
                text += "\n" + report.hint
            common.message(self.app, text)

        def failed(exc: Exception) -> None:
            self.profile_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def run_test(self, *, all_domains: bool = False) -> None:
        selected = self._domains if all_domains else self._selected()
        if not selected:
            common.message(
                self.app,
                _("Отметьте домены галочками или нажмите «Тест всего списка»"),
            )
            return

        self.test_button.set_sensitive(False)
        self.test_all_button.set_sensitive(False)

        def job():
            return cfdomains.test_domains(selected, limit=None, timeout=4)

        def done(results) -> None:
            self.test_button.set_sensitive(True)
            self.test_all_button.set_sensitive(True)
            by_domain: dict[str, list[str]] = {}
            for host, ok, detail in results:
                base = host.split(".", 1)[1] if "." in host else host
                by_domain.setdefault(base, []).append("" if ok else detail)
            ok_count = sum(1 for row in self.store if row[2] == _("OK"))
            for row in self.store:
                domain = row[1]
                if domain not in by_domain:
                    continue
                failures = [item for item in by_domain[domain] if item]
                if failures:
                    row[2] = _("FAIL")
                    row[3] = failures[0][:80]
                else:
                    row[2] = _("OK")
                    row[3] = _("TLS проходит")
            common.toast(
                self.app,
                _("Проверено доменов: {n}").format(n=len(by_domain)),
            )

        def failed(exc: Exception) -> None:
            self.test_button.set_sensitive(True)
            self.test_all_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def add_domain(self) -> None:
        dialog = Gtk.Dialog(title=_("Добавить CF-домен"), transient_for=self.app.window)
        dialog.add_buttons(_("Отмена"), Gtk.ResponseType.CANCEL, _("Добавить"), Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.pack_start(
            common.label(_("Домен TgWsProxy (например, pclead.co.uk):"), wrap=True), False, False, 0
        )
        entry = common.entry()
        entry.set_placeholder_text("example.co.uk")
        box.pack_start(entry, False, False, 0)
        box.show_all()
        response = dialog.run()
        text = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not text:
            return

        def job():
            return api.add_to_cf_domains(self.app.cfg, [text])

        def done(result) -> None:
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            common.toast(
                self.app,
                _("Добавлено: {n}").format(n=len(payload.get("added") or [])),
            )
            self.refresh()

        run_in_thread(job, on_done=done)

    def remove_selected(self) -> None:
        selected = self._selected()
        if not selected:
            common.message(self.app, _("Отметьте домены галочками"))
            return

        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Удалить выбранные домены из hostlist?"),
        )
        confirm.format_secondary_text(
            _(
                "{n} домен(ов) будут убраны из hostlist zapret, служба перезапустится.\n\n"
                "ВНИМАНИЕ: это CF-домены TgWsProxy. Без них прокси не сможет "
                "подключиться к Telegram — zapret перестанет маскировать их SNI.\n\n"
                "Если вы просто хотите проверить домены — используйте «Тест TLS», "
                "он ничего не меняет."
            ).format(n=len(selected))
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return

        self.remove_button.set_sensitive(False)

        def job():
            return api.remove_from_hostlist(self.app.cfg, selected)

        def done(result) -> None:
            self.remove_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            common.toast(
                self.app,
                _("Удалено: {n}").format(n=len(payload.get("removed") or [])),
            )
            self.refresh()

        run_in_thread(job, on_done=done)
