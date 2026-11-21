"""Экран «Диагностика» (SPEC 14).

Вывод doctor в таблице + «Копировать отчёт», «Обновить».
"""

from __future__ import annotations

import time

from gi.repository import GLib, Gdk, Gtk

from ... import api, blockcheck, netdiag, tgws, zapret
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
            tooltip_column=2,
        )
        # Без внутренней прокрутки: высота по числу строк, прокручивается
        # страница целиком. Иначе таблица обрезается на середине.
        self.scrolled = self.tree

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        self.net_button = common.button(
            _("Сетевая диагностика"), lambda _: self.run_network_diag()
        )
        common.pack(self.buttons, self.net_button)
        self.pick_button = common.button(
            _("Подобрать стратегию"), lambda _: self.pick_strategy()
        )
        common.pack(self.buttons, self.pick_button)
        common.pack(self.buttons, common.button(_("Копировать отчёт"), lambda _: self.copy_report()))
        common.pack(self.buttons, common.button(_("Обновить"), lambda _: self.refresh()))

        # Вердикт + рекомендации + сетевые проверки (по мотивам NetFix).
        self.verdict_label = common.label("", wrap=True, css_class="screen-title")
        self.net_store = Gtk.ListStore(str, str, str)
        self.net_tree = common.table_from_store(
            self.net_store,
            [_("Проверка"), _("Статус"), _("Детали")],
            tooltip_column=2,
        )
        # Без ScrolledWindow: TreeView запрашивает высоту по числу строк,
        # внешняя прокрутка страницы уже есть.
        self.net_scrolled = self.net_tree
        # Пока проверка не запущена, пустая таблица с одним заголовком
        # выглядит как поломка — показываем подсказку вместо неё.
        self.net_scrolled.set_no_show_all(True)
        self.net_scrolled.set_visible(False)
        self.reco_label = common.label("", wrap=True)
        self.net_hint = common.label(
            _("Нажмите «Сетевая диагностика», чтобы проверить DC Telegram, "
              "DNS, SNI и скорость медиа"),
            wrap=True,
        )

        # Профили desync — только просмотр: какие правила реально работают.
        self.profile_store = Gtk.ListStore(str, str, str, str)
        self.profile_tree = common.table_from_store(
            self.profile_store,
            [_("Протокол"), _("Статус"), _("Действует на"), _("Стратегия")],
            tooltip_column=3,
        )
        # Высота по контенту: без внутренней прокрутки.
        self.profile_scrolled = self.profile_tree
        self.profile_note = common.label("", wrap=True)

        self._pick_start: float | None = None
        self._pick_timer: int | None = None

        common.pack(self.box, self.buttons)
        common.pack(self.box, self.verdict_label)
        common.pack(self.box, self.net_hint)
        common.pack(self.box, self.net_scrolled)
        common.pack(self.box, self.reco_label)
        common.pack(self.box, self.profile_note)
        common.pack(self.box, self.profile_scrolled)
        common.pack(self.box, self.scrolled)
        self.last_report = None
        self.refresh()
        self.refresh_profiles()

    def _stop_pick_timer(self) -> None:
        if self._pick_timer is not None:
            GLib.source_remove(self._pick_timer)
            self._pick_timer = None

    def _tick_pick(self) -> bool:
        base = self._pick_start or time.monotonic()
        self.verdict_label.set_text(
            _("Подбираю стратегию… (прошло ~{n} минут)").format(n=int((time.monotonic() - base) // 60))
        )
        return True

    def _cancel_strategy_selection(self) -> None:
        """«Отмена подбора»: проверить и поднять zapret/TgWsProxy, если они нужны."""

        self.pick_button.set_sensitive(True)

        def job():
            busy = blockcheck.running() or bool(blockcheck.stray_processes())
            info = zapret.detect_zapret(self.app.cfg.zapret.service)
            if info.active:
                zapret_state = _("active")
            elif busy:
                zapret_state = _("подбор (zapret вернётся автоматически)")
            else:
                code, message = api.start_zapret(self.app.cfg)
                zapret_state = (
                    _("active") if code == api.EXIT_OK else _("не запустился: {msg}").format(msg=message)
                )

            if tgws.service_state() == "active":
                tgws_state = _("active")
            else:
                code, message = api.start_tgws(self.app.cfg)
                tgws_state = (
                    _("active") if code == api.EXIT_OK else _("не запустился: {msg}").format(msg=message)
                )
            return zapret_state, tgws_state

        def done(result):
            zapret_state, tgws_state = result
            common.message(
                self.app,
                _("Подбор отменён.\n\nZapret2: {zap}\nTgWsProxy: {tg}").format(
                    zap=zapret_state, tg=tgws_state
                ),
            )

        def failed(exc: Exception) -> None:
            common.message(self.app, _("Ошибка при отмене подбора: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def pick_strategy(self) -> None:
        """Подобрать рабочую стратегию desync через blockcheck2.

        Пользователь должен понимать: zapret на время замеров остановится.
        """

        from ... import blockcheck

        if blockcheck.scan_in_progress():
            common.message(
                self.app,
                _("Подбор стратегии уже идёт — дождитесь окончания замеров."),
            )
            return

        ok, reason = api.blockcheck_status(self.app.cfg)

        dialog = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Подобрать стратегию desync?"),
        )
        dialog.format_secondary_text(
            _(
                "Запустится штатный blockcheck2.sh из zapret. Он подберёт "
                "стратегии, которые обходят вашего провайдера.\n\n"
                "zapret на время замеров будет ОСТАНОВЛЕН и включён обратно — "
                "это обязательно, иначе он вмешивается в замеры.\n\n"
                "Обычно занимает 15–35 минут: тесты прогоняются долго, "
                "зависит от вашего провайдера.\n\n"
                "Подбор ничего не меняет в конфиге: вы просто увидите список "
                "рабочих стратегий."
            )
            + ("\n\n" + _("Сейчас: {reason}").format(reason=reason) if not ok else "")
        )
        dialog.add_button(_("Отмена"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Подобрать"), 1)
        dialog.add_button(_("Отмена подбора"), 2)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response == 2:
            self._cancel_strategy_selection()
            return
        if response != 1:
            return

        self.pick_button.set_sensitive(False)
        self.verdict_label.set_text(
            _("Подбираю стратегию… ожидаем завершения прогона")
        )
        self._pick_start = time.monotonic()
        self._pick_timer = GLib.timeout_add_seconds(10, self._tick_pick)

        def job():
            return api.pick_strategy(self.app.cfg, level="quick", timeout=2100)

        def done(result) -> None:
            self._stop_pick_timer()
            self.pick_button.set_sensitive(True)
            code, payload = result
            strategies = payload.get("strategies") or []
            best = payload.get("best")
            timed_out = "прерван" in str(payload.get("error") or "")
            if not strategies:
                message = str(payload.get("error") or _("см. Логи"))
                if timed_out:
                    header = _(
                        "Стратегии не найдены и прогон не завершён "
                        "(проверены не все варианты)"
                    )
                else:
                    header = _("Стратегии не найдены.")
                common.message(self.app, f"{header}\n\n{message}")
                self.verdict_label.set_text(
                    _("Прогон не завершён — проверены не все варианты")
                    if timed_out
                    else _("Стратегии не нужны: {note}").format(note=message)
                )
                return
            lines = [
                _("Найдено стратегий: {n}").format(n=len(strategies)),
                "",
            ]
            for item in strategies[:5]:
                mark = "★" if best and item["args"] == best["args"] else " "
                lines.append(
                    f"{mark} {item['success']}/{item['total']}: {item['args'][:110]}"
                )
            lines.append("")
            if timed_out:
                # Даже если стратегии есть, они могли провериться
                # не все варианты: добавляем честную оговорку.
                lines.append(str(payload["error"]))
                lines.append("")
            if payload.get("error") and not timed_out:
                lines.append(str(payload["error"]))
                lines.append("")
            lines.append(
                _(
                    "Скопируйте нужную строку и вставьте в NFQWS2_OPT файла "
                    "config (заменив строку своего профиля), затем перезапустите "
                    "zapret. LinFix конфиг не меняет — правка остаётся за вами."
                )
            )
            self.verdict_label.set_text("\n".join(lines))
            common.toast(
                self.app,
                _("Найдено стратегий: {n}, zapret снова запущен").format(n=len(strategies)),
            )

        def failed(exc: Exception) -> None:
            self._stop_pick_timer()
            self.pick_button.set_sensitive(True)
            common.message(self.app, _("Ошибка подбора: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

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
            self.net_hint.set_visible(False)
            self.net_scrolled.set_visible(True)
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
