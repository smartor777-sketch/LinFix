"""Экран «Логи» (SPEC 14).

journalctl zapret2 + journalctl --user tgwsproxy + хвост proxy.log, поиск.
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from ... import tgws, util
from ..i18n import _
from ..worker import run_in_thread
from . import common


class LogsScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Логи"))

        self.textview = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD)
        self.buffer = self.textview.get_buffer()
        self.scrolled = common.scrolled(self.textview)

        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        self.entry = common.entry()
        self.entry.set_placeholder_text(_("Поиск в логах"))
        self.entry.connect("activate", lambda *_: self.filter_text())
        self.b_refresh = common.button(_("Обновить"), lambda _: self.refresh())
        self.b_copy = common.button(_("Копировать"), lambda _: self.copy_logs())
        common.pack_expand(self.search_box, self.entry)
        common.pack(self.search_box, self.b_refresh)
        common.pack(self.search_box, self.b_copy)

        # Поиск и «Обновить» — над логом: так их видно без прокрутки.
        common.pack(self.box, self.search_box)
        common.pack_expand(self.box, self.scrolled)
        self.full_text = ""
        self.refresh()

    def refresh(self) -> None:
        def job():
            parts: list[str] = []
            completed = util.run(
                ["journalctl", "-u", self.app.cfg.zapret.service, "-n", "200", "--no-pager"],
                check=False,
                timeout=20,
            )
            if completed.stdout:
                parts.append(f"=== {self.app.cfg.zapret.service} ===\n{completed.stdout}")
            completed = util.run(
                ["journalctl", "--user", "-u", tgws.UNIT_NAME, "-n", "100", "--no-pager"],
                check=False,
                timeout=20,
            )
            if completed.stdout:
                parts.append(f"=== {tgws.UNIT_NAME} (user) ===\n{completed.stdout}")
            log_path = tgws.find_log_path()
            if log_path is not None:
                try:
                    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                except OSError:
                    tail = []
                if tail:
                    parts.append(f"=== {log_path} ===\n{'\n'.join(tail)}")
            return "\n".join(parts) or _("Пусто")

        def apply_text(text):
            self.full_text = text
            self.buffer.set_text(text)
            self.entry.set_text("")

        run_in_thread(job, on_done=apply_text)

    def filter_text(self) -> None:
        query = self.entry.get_text().strip()
        if not query:
            self.buffer.set_text(self.full_text)
            return
        lines = [line for line in self.full_text.splitlines() if query.lower() in line.lower()]
        self.buffer.set_text("\n".join(lines) or _("Нет совпадений"))

    def copy_logs(self) -> None:
        """Скопировать в буфер то, что сейчас показано в окне логов.

        Копируется видимый текст, а не весь лог: если пользователь
        отфильтровал строки, в буфере окажется именно выборка.
        """

        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, include_hidden_chars=True)
        if not text.strip():
            common.toast(self.app, _("Логи пусты — нечего копировать"))
            return
        clipboard = Gtk.Clipboard.get_for_display(
            Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
        )
        # set_text ждёт длину в БАЙТАХ: при кириллице len() короче, и
        # хвост строки молча терялся.
        clipboard.set_text(text, len(text.encode("utf-8")))
        lines = text.count("\n") + 1
        common.toast(self.app, _("Логи скопированы ({n} строк)").format(n=lines))
