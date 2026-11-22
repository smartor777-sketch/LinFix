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

#: Какой список показывать в таблице.
SOURCE_GITHUB = "github"
SOURCE_USER = "user"
SOURCE_MODES = (SOURCE_GITHUB, SOURCE_USER)


class DomainsScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Домены"))

        # Какой список показывать: сохранённый с GitHub или текущий
        # пользовательский (он же — реальная ротация TgWsProxy).
        self.source_combo = Gtk.ComboBoxText()
        self.source_combo.append(SOURCE_GITHUB, _("Список с GitHub"))
        self.source_combo.append(SOURCE_USER, _("Пользовательский (ротация)"))
        self.source_combo.set_active_id(SOURCE_GITHUB)
        self.source_combo.connect("changed", self._on_source_changed)
        source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=4)
        common.pack(source_row, common.label(_("Показать:")))
        common.pack(source_row, self.source_combo)
        self._source: str = SOURCE_GITHUB

        self.info = common.label(_("—"), wrap=True)
        self.note = common.label(_(""), wrap=True)

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
        self.add_rotation_button = common.button(
            _("Добавить в ротацию"), lambda _: self.add_rotation_domain()
        )
        self.remove_rotation_button = common.button(
            _("Удалить из ротации"), lambda _: self.remove_rotation_domain()
        )
        for button in (
            self.refresh_button,
            self.test_button,
            self.test_all_button,
            self.profile_button,
            self.add_button,
            self.remove_button,
            self.add_rotation_button,
            self.remove_rotation_button,
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
        common.pack(self.box, source_row)
        common.pack(self.box, self.info)
        common.pack(self.box, self.note)
        common.pack(self.box, self.buttons)
        common.pack_expand(self.box, self.scrolled)

        self._domains: list[str] = []
        self._payload: dict[str, object] = {}
        self._sync_buttons()
        self.refresh()

    # ------------------------------------------------------------------

    def on_shown(self) -> None:
        self.refresh()

    def _on_source_changed(self, _combo=None) -> None:
        source = self.source_combo.get_active_id() or SOURCE_GITHUB
        if source == self._source:
            return
        self._source = source
        self._fill_table()
        self._sync_buttons()

    def _domains_for_source(self, payload: dict[str, object]) -> list[str]:
        if self._source == SOURCE_USER:
            return list(payload.get("user_domains") or [])
        return list(payload.get("domains") or [])

    def _fill_table(self) -> None:
        """Заполнить таблицу выбранным списком, сохранив отметки пользователя."""

        payload = self._payload or {}
        missing = set(payload.get("missing") or [])
        previous = {row[1] for row in self.store if row[0]}
        self._domains = self._domains_for_source(payload)
        self.store.clear()
        for domain in self._domains:
            if self._source == SOURCE_GITHUB:
                status = _("MISSING") if domain in missing else _("OK")
            else:
                status = _("ROTATION")
            self.store.append([domain in previous, domain, status, ""])

    def _sync_buttons(self) -> None:
        """Кнопки, которые меняют списки, имеют смысл только для своего источника."""

        github = self._source == SOURCE_GITHUB
        user = self._source == SOURCE_USER
        # show_all() в GTK игнорирует set_visible(False), поэтому скрытые
        # кнопки помечаем no_show_all: иначе после show_all они снова видны.
        mapping = {
            self.refresh_button: github,
            self.add_button: github,
            self.remove_button: github,
            self.add_rotation_button: user,
            self.remove_rotation_button: user,
            self.test_button: True,
            self.test_all_button: True,
            self.profile_button: True,
        }
        for button, visible in mapping.items():
            button.set_no_show_all(not visible)
            button.set_visible(visible)
            if visible:
                button.show()

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
            self._payload = dict(payload)
            self._fill_table()
            self._sync_buttons()
            self._update_info()

        run_in_thread(job, on_done=done)

    def _update_info(self) -> None:
        payload = self._payload or {}
        if self._source == SOURCE_USER:
            active = payload.get("active_mode") == api.ROTATION_USER
            self.info.set_text(
                _("Пользовательских доменов: {n}. {state}").format(
                    n=len(payload.get("user_domains") or []),
                    state=_("Используются прокси сейчас.")
                    if active
                    else _("Сохранены, но прокси сейчас в режиме GitHub."),
                )
            )
            self.note.set_text(
                _(
                    "Это текущая ротация TgWsProxy: именно эти домены прокси "
                    "перебирает при подключении. Добавление и удаление меняет "
                    "конфиг прокси и перезапускает службу."
                )
            )
        else:
            missing = payload.get("missing") or []
            updated = payload.get("updated_at") or _("никогда")
            self.info.set_text(
                _("Доменов: {n}, в hostlist zapret: {m}, обновлено: {when}").format(
                    n=len(payload.get("domains") or []),
                    m=payload.get("in_hostlist", 0),
                    when=updated,
                )
                + (
                    _(" — нет в hostlist: {n}").format(n=len(missing)) if missing else ""
                )
            )
            self.note.set_text(
                _(
                    "Список с GitHub для hostlist zapret — это не ротация "
                    "прокси. Все домены должны быть в hostlist zapret, иначе "
                    "прокси ходит через заблокированный SNI."
                )
            )

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
                "Это может сразу сломать доступ к Telegram, включая медиа.\n\n"
                "Что произойдёт:\n"
                "• TgWsProxy перестанет использовать ваш проверенный список "
                "и начнёт брать домены из списка на GitHub;\n"
                "• включится автообновление этого списка;\n"
                "• конфиг прокси изменится, служба перезапустится, текущие "
                "соединения оборвутся.\n\n"
                "Что учесть:\n"
                "• список на GitHub закодирован, но клиент его декодирует и "
                "получает те же домены, что и у вас;\n"
                "• рабочий профиль zapret для CF-доменов привязан к вашему "
                "списку — новые имена в hostlist сами не попадут;\n"
                "• если upstream сменит домены, медиа может перестать "
                "загружаться до ручного обновления hostlist.\n\n"
                "Когда это допустимо:\n"
                "• только если пользовательский набор доменов полностью "
                "перестал работать (проверено «Тест TLS» и «Проверить профиль "
                "CF»), и вы хотите попробовать официальный список.\n\n"
                "Перед переключением стоит сделать бэкап конфига: "
                "linfix tgws config сохраняет резервную копию сам.\n\n"
                "Ваш список сохранится — вернуться можно тем же выпадающим "
                "списком или через «Пользовательские домены»."
            )
        else:
            question = _("Вернуть ротацию на пользовательские домены?")
            detail = _(
                "Будет использован ваш сохранённый список доменов, "
                "автообновление с GitHub выключится.\n\n"
                "Это штатный рабочий режим. Конфиг прокси изменится, служба "
                "перезапустится, соединения на несколько секунд оборвутся.\n\n"
                "Если список пуст, переключение не выполнится — сначала "
                "добавьте домены в режиме «Пользовательский (ротация)»."
            )
        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING
            if target == api.ROTATION_GITHUB
            else Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=question,
        )
        confirm.format_secondary_text(detail)
        confirm.add_button(_("Отмена"), Gtk.ResponseType.CANCEL)
        risky = target == api.ROTATION_GITHUB
        confirm.add_button(
            _("Всё равно переключить") if risky else _("Переключить"),
            Gtk.ResponseType.OK,
        )
        # Enter не должен случайно подтверждать опасное переключение.
        confirm.set_default_response(Gtk.ResponseType.CANCEL)
        confirm.show_all()
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

    def _edit_rotation(
        self, *, add: list[str] | None = None, remove: list[str] | None = None
    ) -> None:
        def job():
            return api.update_user_rotation_domains(self.app.cfg, add=add, remove=remove)

        def done(result) -> None:
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            if payload.get("saved_only"):
                common.toast(
                    self.app,
                    _(
                        "Список сохранён. Прокси в режиме GitHub — переключите "
                        "ротацию на «Пользовательские домены», чтобы применить."
                    ),
                )
            elif payload.get("changed"):
                common.toast(
                    self.app,
                    _("Ротация обновлена: {n} доменов").format(
                        n=len(payload.get("domains") or [])
                    ),
                )
                if payload.get("restarted") is False:
                    common.message(
                        self.app,
                        _("Список сохранён, но служба не перезапустилась — перезапустите TgWsProxy"),
                    )
            self.refresh()

        def failed(exc: Exception) -> None:
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def add_rotation_domain(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Добавить домен в ротацию"), transient_for=self.app.window
        )
        dialog.add_buttons(
            _("Отмена"), Gtk.ResponseType.CANCEL, _("Добавить"), Gtk.ResponseType.OK
        )
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.pack_start(
            common.label(
                _("Домен для ротации TgWsProxy (например, pclead.co.uk):"), wrap=True
            ),
            False,
            False,
            0,
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
        self._edit_rotation(add=[text])

    def remove_rotation_domain(self) -> None:
        selected = self._selected()
        if not selected:
            common.message(self.app, _("Отметьте домены галочками"))
            return
        if len(selected) >= len(self._domains):
            common.message(
                self.app,
                _("Нельзя удалить все домены: ротация останется пустой"),
            )
            return
        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Убрать выбранные домены из ротации?"),
        )
        confirm.format_secondary_text(
            _(
                "{n} домен(ов) будут убраны из пользовательской ротации, "
                "конфиг прокси обновится, служба перезапустится.\n\n"
                "Список hostlist zapret при этом не меняется."
            ).format(n=len(selected))
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self._edit_rotation(remove=selected)

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
