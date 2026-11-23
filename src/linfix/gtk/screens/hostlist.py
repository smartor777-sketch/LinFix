"""Экран «Списки»: hostlist zapret — просмотр, добавление, удаление, импорт."""

from __future__ import annotations

import tempfile
from pathlib import Path

from gi.repository import Gtk

from ... import api, hostlist
from ..i18n import _
from ..worker import run_in_thread
from . import common

#: Сколько строк показывать в таблице. Список бывает 30k+: отрисовка всех
#: заметно тормозит, а найти нужное быстрее фильтром (он смотрит весь список).
SHOW_LIMIT = 100

#: CF-домены критичны для TgWsProxy — их удаление требует двойного подтверждения.
SOURCE_LABELS = {
    "cf": _("CF (прокси) — нужен Telegram"),
    "user": _("свои"),
    "other": _("из zapret"),
}


class HostlistScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Списки"))

        self.info = common.label(_("—"), wrap=True)

        # ввод доменов списком
        self.entry = common.entry()
        self.entry.set_placeholder_text(_("домены через пробел, запятую или с новой строки"))

        self.store = Gtk.ListStore(str, str)
        self.store.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate((_("Домен"), _("Источник"))):
            renderer = Gtk.CellRendererText()
            self.tree.append_column(
                Gtk.TreeViewColumn(title, renderer, text=index)
            )
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.filter_entry = common.entry()
        self.filter_entry.set_placeholder_text(_("Фильтр по подстроке"))
        self.filter_entry.connect("changed", lambda *_: self.refresh())

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        self.add_button = common.button(_("Добавить"), lambda _: self.add_selected())
        self.remove_button = common.button(_("Удалить выбранное"), lambda _: self.remove_selected())
        self.import_button = common.button(_("Импорт URL / файла"), lambda _: self.import_dialog())
        self.refresh_button = common.button(_("Обновить"), lambda _: self.refresh())
        for button in (self.add_button, self.remove_button, self.import_button, self.refresh_button):
            common.pack(self.buttons, button)

        self.sources_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        self.sources_combo = Gtk.ComboBoxText()
        self.sources_combo.set_hexpand(True)
        self.load_button = common.button(_("Загрузить список"), lambda _: self.load_source())
        common.pack_expand(self.sources_box, self.sources_combo)
        common.pack(self.sources_box, self.load_button)

        common.pack(self.box, self.info)
        common.pack(self.box, self.entry)
        common.pack(self.box, self.filter_entry)
        common.pack_expand(self.box, common.scrolled(self.tree))
        common.pack(self.box, self.buttons)
        common.pack(self.box, self.sources_box)

        self._entries: list[hostlist.HostlistEntry] = []
        self._base_info = ""
        self._load_sources_combo()
        self.refresh()

    # ------------------------------------------------------------------

    def on_shown(self) -> None:
        self.refresh()

    def _load_sources_combo(self) -> None:
        self.sources_combo.remove_all()
        try:
            sources = api.hostlist_sources(self.app.cfg)
        except Exception:  # noqa: BLE001 — zapret может быть не установлен
            sources = []
        for script, description in sources:
            self.sources_combo.append(script, f"{description} ({script})")
        if sources:
            self.sources_combo.set_active(0)
        self.sources_combo.set_sensitive(bool(sources))
        self.load_button.set_sensitive(bool(sources))

    def refresh(self) -> None:
        def job():
            return api.get_hostlist_entries(self.app.cfg)

        def done(result) -> None:
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                self.info.set_text(str(payload.get("error") or _("Не удалось прочитать hostlist")))
                return
            counts = payload.get("counts", {})
            self._base_info = _(
                "Hostlist: {path} — всего {total} (CF {cf}, свои {user}, из zapret {other})"
            ).format(
                path=payload.get("hostlist", "—"),
                total=payload.get("total", 0),
                cf=counts.get("cf", 0),
                user=counts.get("user", 0),
                other=counts.get("other", 0),
            )
            self._entries = [
                hostlist.HostlistEntry(item["domain"], item["source"])
                for item in payload.get("entries", [])
            ]
            self._render()

        run_in_thread(job, on_done=done)

    def _render(self) -> None:
        """Показать домены; фильтр при этом ищет по всему списку.

        Список бывает 30k+ строк, и отрисовка всех тормозит. Поэтому
        показываем первые LIMIT совпадений, но поиск идёт по всем записям:
        если домен есть в конце списка, фильтр его найдёт.
        """

        needle = self.filter_entry.get_text().strip().lower()
        self.store.clear()
        matched = 0
        shown = 0
        for entry in self._entries:
            if needle and needle not in entry.domain.lower():
                continue
            matched += 1
            if shown >= SHOW_LIMIT:
                continue
            self.store.append([entry.domain, SOURCE_LABELS.get(entry.source, entry.source)])
            shown += 1

        base = getattr(self, "_base_info", "") or ""
        if matched > shown:
            note = _("Показаны первые {shown} из {matched}; поиск идёт по всему списку.").format(
                shown=shown, matched=matched
            )
        elif needle:
            note = _("Найдено: {n}").format(n=matched)
        else:
            note = ""
        self.info.set_text(f"{base}\n{note}".strip() if note else base)

    # ------------------------------------------------------------------

    def _selected_domains(self) -> list[str]:
        model, paths = self.tree.get_selection().get_selected_rows()
        return [model[path][0] for path in paths]

    def add_selected(self) -> None:
        raw = self.entry.get_text()
        domains = hostlist.parse_list_text(raw)
        if not domains:
            common.message(self.app, _("Введите хотя бы один корректный домен"))
            return
        self._run(api.add_to_hostlist, domains)

    def remove_selected(self) -> None:
        domains = self._selected_domains()
        if not domains:
            common.message(self.app, _("Выберите домены в таблице"))
            return
        chosen = set(domains)
        removable = [
            entry.domain for entry in self._entries
            if entry.domain in chosen and entry.source in {"cf", "user"}
        ]
        critical = [
            entry.domain for entry in self._entries
            if entry.domain in chosen and entry.source == "cf"
        ]
        if not removable:
            common.message(
                self.app,
                _("Выбраны домены из чужого списка zapret — их LinFix не меняет"),
            )
            return

        # Удаление CF-домена ломает Telegram-прокси: спрашиваем отдельно и
        # настойчиво, чтобы нельзя было сделать это «случайно».
        if critical:
            first = Gtk.MessageDialog(
                transient_for=self.app.window,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text=_("Вы удаляете домены TgWsProxy ({n})").format(n=len(critical)),
            )
            first.format_secondary_text(
                _(
                    "Без них zapret перестанет маскировать SNI этих доменов, и "
                    "Telegram-прокси не сможет подключиться.\n\n{list}\n\n"
                    "Удалять только если вы точно знаете, зачем."
                ).format(list="\n".join(critical[:8]))
            )
            if first.run() != Gtk.ResponseType.OK:
                first.destroy()
                return
            first.destroy()

            second = Gtk.MessageDialog(
                transient_for=self.app.window,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text=_("Точно удалить? Telegram-прокси сломается"),
            )
            second.format_secondary_text(
                _("Это второй и последний вопрос. Отмена — безопасный выбор.")
            )
            confirmed = second.run() == Gtk.ResponseType.OK
            second.destroy()
            if not confirmed:
                return

        self._run(api.remove_from_hostlist, removable)

    def _run(self, fn, domains: list[str]) -> None:
        self.add_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)

        def job():
            return fn(self.app.cfg, domains)

        def done(result) -> None:
            self.add_button.set_sensitive(True)
            self.remove_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка")))
                return
            added = payload.get("added") or payload.get("removed") or []
            skipped = payload.get("skipped") or []
            message = _("Готово: {added}").format(added=len(added))
            if skipped:
                message += _(", пропущено {n}").format(n=len(skipped))
            if payload.get("restarted") is False:
                message += _(" (zapret не перезапущен)")
            common.toast(self.app, message)
            self.entry.set_text("")
            self.refresh()

        def failed(exc: Exception) -> None:
            self.add_button.set_sensitive(True)
            self.remove_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    # ------------------------------------------------------------------

    def import_dialog(self) -> None:
        dialog = Gtk.Dialog(title=_("Импорт списка доменов"), transient_for=self.app.window)
        dialog.add_buttons(_("Отмена"), Gtk.ResponseType.CANCEL, _("Импортировать"), Gtk.ResponseType.OK)
        dialog.set_default_size(560, 220)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        url_entry = common.entry()
        url_entry.set_placeholder_text("https://example.com/list.txt")
        file_entry = common.entry()
        file_entry.set_placeholder_text(_("/путь/к/списку.txt"))

        box.pack_start(common.label(_("Ссылка на список (HTTPS):")), False, False, 0)
        box.pack_start(url_entry, False, False, 0)
        box.pack_start(common.label(_("…или локальный файл:")), False, False, 0)
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        common.pack_expand(file_row, file_entry)
        choose = common.button(_("Выбрать…"), lambda _: self._choose_file(file_entry, dialog))
        common.pack(file_row, choose)
        box.pack_start(file_row, False, False, 0)
        box.show_all()

        response = dialog.run()
        url = url_entry.get_text().strip()
        path = file_entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        if not url and not path:
            common.message(self.app, _("Укажите ссылку или файл"))
            return

        self.import_button.set_sensitive(False)

        def job():
            return api.import_hostlist(self.app.cfg, url=url or None, list_file=path or None)

        def done(result) -> None:
            self.import_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка импорта")))
                return
            common.toast(
                self.app,
                _("Импортировано: {n}, пропущено: {s}").format(
                    n=len(payload.get("added") or []), s=len(payload.get("skipped") or [])
                ),
            )
            self.refresh()

        def failed(exc: Exception) -> None:
            self.import_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def _choose_file(self, entry: Gtk.Entry, dialog: Gtk.Dialog) -> None:
        chooser = Gtk.FileChooserDialog(
            title=_("Выбрать файл со списком"),
            transient_for=dialog,
            action=Gtk.FileChooserAction.OPEN,
        )
        chooser.add_buttons(_("Отмена"), Gtk.ResponseType.CANCEL, _("Открыть"), Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK:
            entry.set_text(chooser.get_filename() or "")
        chooser.destroy()

    def load_source(self) -> None:
        script = self.sources_combo.get_active_id()
        if not script:
            return
        self.load_button.set_sensitive(False)

        def job():
            return api.load_hostlist_source(self.app.cfg, script)

        def done(result) -> None:
            self.load_button.set_sensitive(True)
            code, payload = result
            if code != api.EXIT_OK or payload.get("ok") is False:
                common.message(self.app, str(payload.get("error") or _("Ошибка загрузки")))
                return
            common.toast(self.app, _("Список загружен: {script}").format(script=script))
            self.refresh()

        def failed(exc: Exception) -> None:
            self.load_button.set_sensitive(True)
            common.message(self.app, _("Ошибка: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)
