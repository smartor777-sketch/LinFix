"""Экран «Домены» (SPEC 14).

Список CF-доменов, дата обновления, кнопка «Обновить и проверить»,
таблица TLS-тестов.
"""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, cfdomains, state
from ..i18n import _
from ..worker import run_in_thread
from . import common


class DomainsScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Домены"))

        self.info = common.label(_("—"), wrap=True)
        self.store = Gtk.ListStore(str, str, str)
        self.tree = common.table_from_store(
            self.store,
            [_("Хост"), _("Статус"), _("Детали")],
        )
        self.scrolled = common.scrolled(self.tree)

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        common.pack(self.buttons, common.button(_("Обновить и проверить"), lambda _: self.run_update()))
        common.pack(self.buttons, common.button(_("Тест TLS"), lambda _: self.run_test()))

        common.pack(self.box, self.info)
        common.pack_expand(self.box, self.scrolled)
        common.pack(self.box, self.buttons)

        self.refresh()

    def refresh(self) -> None:
        def job():
            source, hostlist, present = api.get_domains(self.app.cfg)
            state_info = state.load_state()
            info_text = (
                f"{_('Источник:')} {source.source}, {_('Доменов:')} {len(source.domains)}, "
                f"Hostlist: {hostlist or '—'}, {_('Обновлено:')} {state_info.domains_updated_at or _('никогда')}"
            )
            rows = [
                (domain, _("MISSING") if domain not in present else _("OK"), "")
                for domain in source.domains
            ]
            return info_text, rows

        def apply_data(data):
            info_text, rows = data
            self.info.set_text(info_text)
            self.store.clear()
            for domain, status, detail in rows:
                self.store.append([domain, status, detail])

        run_in_thread(job, on_done=apply_data)

    def run_update(self) -> None:
        def job():
            return api.update_domains(self.app.cfg, force=True)

        def done(result):
            code, payload = result
            self.refresh()
            if not payload.get("ok"):
                common.message(self.app, str(payload.get("error", _("Ошибка"))))

        run_in_thread(job, on_done=done)

    def run_test(self) -> None:
        source, hostlist, present = api.get_domains(self.app.cfg)
        if not source.domains:
            common.toast(self.app, _("Нет доменов для теста"))
            return
        selected = source.domains[:3]

        def job():
            return cfdomains.test_domains(selected, limit=3, timeout=3)

        def apply_data(results):
            self.store.clear()
            for domain in selected:
                status = _("MISSING") if domain not in present else _("OK")
                detail = ""
                for host, ok, det in results:
                    if host.endswith("." + domain) and not ok:
                        detail = det
                self.store.append([domain, status, detail])

        run_in_thread(job, on_done=apply_data)
