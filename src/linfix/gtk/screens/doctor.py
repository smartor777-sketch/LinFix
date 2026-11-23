"""Экран «Диагностика» (SPEC 14).

Вывод doctor в таблице + «Копировать отчёт», «Обновить».
"""

from __future__ import annotations

import time

from gi.repository import GLib, Gdk, Gtk

from ... import api, blockcheck, netdiag, netdiag_history, tgws, util, zapret
from ..i18n import _
from ..worker import run_in_thread
from . import common


class DoctorScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Диагностика"))

        # --- Баннер проблемы: висячие IPv6-заглушки --------------------------
        # Стоит выше таблиц: если весь IPv6 уходит в blackhole, остальные
        # результаты проверок вводят в заблуждение, а причина — сверху.
        self.banner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=6
        )
        # Скрыт до первого отчёта; флаг снимаем при показе, иначе
        # show_all() не покажет виджет с no-show-all.
        self.banner.set_no_show_all(True)
        self.banner.set_visible(False)
        self.banner_label = common.label("", wrap=True, css_class="banner-error")
        self.banner_button = common.button(
            _("Убрать заглушки"), lambda _: self.clear_ipv6_blackholes()
        )
        self.banner_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        common.pack(self.banner_buttons, self.banner_button)
        common.pack(self.banner, self.banner_label)
        common.pack(self.banner, self.banner_buttons)
        common.pack(self.box, self.banner)

        self.store = Gtk.ListStore(str, str, str, str)
        self.tree = common.table_from_store(
            self.store,
            [_("Проверка"), _("Статус"), _("Детали"), _("Подсказка")],
            tooltip_column=2,
        )
        # Без внутренней прокрутки: высота по числу строк, прокручивается
        # страница целиком. Иначе таблица обрезается на середине.
        self.scrolled = self.tree

        # --- Блок 1: сетевая диагностика -------------------------------------
        # Кнопка и её результаты — по соседству, чтобы было видно, что
        # таблица ниже относится именно к этой проверке.
        self.net_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=4
        )
        self.net_button = common.button(
            _("Обновить"), lambda _: self.run_network_diag()
        )
        common.pack(self.net_buttons, self.net_button)
        self.history_button = common.button(
            _("Очистить историю"), lambda _: self.clear_history()
        )
        common.pack(self.net_buttons, self.history_button)
        self.net_caption = common.label(
            _(
                "История прогонов: время, вердикт и были ли доступны DC и "
                "прокси. «Обновить» запускает новый прогон (до 20 секунд) и "
                "добавляет его в историю. Для разбора конкретного сайта "
                "используйте раздел «Сайты»."
            ),
            wrap=True,
        )

        # --- Блок 2: подбор стратегии desync ---------------------------------
        self.strategy_caption = common.label(
            _(
                "Стратегия (desync) — это способ, которым zapret обрабатывает "
                "первые пакеты TLS-соединения, чтобы провайдер не распознал "
                "домен и не заблокировал его.\n"
                "Кнопка запускает штатный blockcheck2 из zapret: он на время "
                "замеров останавливает zapret и перебирает десятки готовых "
                "вариантов маскировки — fake, multisplit, multidisorder и их "
                "комбинации — на выбранных ниже доменах.\n"
                "В результате вы получите список стратегий с оценкой вида "
                "«4/4» и увидите, какие из них открывают домены у вашего "
                "провайдера. Конфиг при этом не меняется: применить найденное "
                "нужно вручную."
            ),
            wrap=True,
        )
        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        self.pick_button = common.button(
            _("Подобрать стратегию"), lambda _: self.pick_strategy()
        )
        common.pack(self.buttons, self.pick_button)
        # Кнопка «отменить и восстановить» — видна во время прогона подбора.
        self.cancel_button = common.button(
            _("Отмена подбора"), lambda _: self._cancel_strategy_selection()
        )
        self.cancel_button.set_visible(False)
        common.pack(self.buttons, self.cancel_button)
        common.pack(self.buttons, common.button(_("Копировать отчёт"), lambda _: self.copy_report()))

        # Вердикт + рекомендации + сетевые проверки (по мотивам NetFix).
        self.verdict_label = common.label("", wrap=True, css_class="screen-title")
        self.net_store = Gtk.ListStore(str, str, str, str, str)
        self.net_tree = common.table_from_store(
            self.net_store,
            [_("Когда"), _("Вердикт"), _("Напрямую"), _("Прокси"), _("Медиа")],
            tooltip_column=1,
        )
        self.net_tree.connect("row-activated", self._on_history_row)
        # Без ScrolledWindow: TreeView запрашивает высоту по числу строк,
        # внешняя прокрутка страницы уже есть.
        self.net_scrolled = self.net_tree
        # Пока проверка не запущена, пустая таблица с одним заголовком
        # выглядит как поломка — показываем подсказку вместо неё.
        self.net_scrolled.set_no_show_all(True)
        self.net_scrolled.set_visible(False)
        self.reco_label = common.label("", wrap=True)
        self.net_hint = common.label(
            _("История пуста. Нажмите «Обновить», чтобы проверить "
              "дата-центры, DNS, задержки и скорость медиа"),
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

        # Порядок: сначала диагностика со своими результатами, затем
        # пояснение и кнопки подбора, затем профили и общий отчёт.
        common.pack(self.box, self.net_buttons)
        common.pack(self.box, self.net_caption)
        common.pack(self.box, self.verdict_label)
        common.pack(self.box, self.net_hint)
        common.pack(self.box, self.net_scrolled)
        common.pack(self.box, self.reco_label)

        common.pack(self.box, self.strategy_caption)
        common.pack(self.box, self.buttons)
        # Краткая информация по стратегиям — отдельной строкой под кнопками,
        # а не между «Подобрать стратегию» и «Отмена подбора».
        self.strat_label = common.label(_("Текущая: —"), css_class="strategy-info")
        self.strat_label.set_margin_bottom(2)
        common.pack(self.box, self.strat_label)

        common.pack(self.box, self.profile_note)
        common.pack(self.box, self.profile_scrolled)
        common.pack(self.box, self.scrolled)
        self.last_report = None
        # Результат подбора живёт только в памяти этого запуска GUI:
        # старые ошибки blockcheck не должны видеть при следующем старте.
        self._pick_result: blockcheck.BlockcheckResult | None = None
        self._history: list[netdiag_history.HistoryEntry] = []
        self.refresh()
        self.refresh_profiles()
        self.refresh_netdiag_history()

    def _stop_pick_timer(self) -> None:
        if self._pick_timer is not None:
            GLib.source_remove(self._pick_timer)
            self._pick_timer = None

    def _tick_pick(self) -> bool:
        base = self._pick_start or time.monotonic()
        elapsed = int((time.monotonic() - base) // 60)
        self.verdict_label.set_text(
            _("⏳ Идёт подбор стратегии… прошло ~{n} мин").format(n=elapsed)
        )
        return True

    def _services_state(self) -> tuple[str, str]:
        """Проверить состояние zapret/TgWsProxy и поднять их при необходимости."""

        busy = blockcheck.running() or bool(blockcheck.stray_processes())
        info = zapret.detect_zapret(self.app.cfg.zapret.service)
        if info.active:
            zapret_state = _("active")
        elif busy:
            zapret_state = _("подбор (zapret вернётся автоматически)")
        else:
            code, message = api.start_zapret(self.app.cfg)
            zapret_state = (
                _("active")
                if code == api.EXIT_OK
                else _("не запустился: {msg}").format(msg=message)
            )

        if tgws.service_state() == "active":
            tgws_state = _("active")
        else:
            code, message = api.start_tgws(self.app.cfg)
            tgws_state = (
                _("active")
                if code == api.EXIT_OK
                else _("не запустился: {msg}").format(msg=message)
            )
        return zapret_state, tgws_state

    def _cancel_strategy_selection(self) -> None:
        """«Отмена подбора»: остановить скан при необходимости + проверить/поднять службы."""

        def job():
            if blockcheck.running() or blockcheck.stray_processes():
                try:
                    util.run_helper(
                        "zapret.blockcheck_stop",
                        ["--service", self.app.cfg.zapret.service],
                        timeout=150,
                    )
                except Exception:  # noqa: BLE001 — не блокировать UI
                    pass
                # Helper возвращает замеряемый helper в работу: дождёмся,
                # пока маркер уйдёт и zapret вернётся.
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if not (blockcheck.running() or blockcheck.stray_processes()):
                        break
                    time.sleep(0.5)
            return self._services_state()

        def done(result):
            zapret_state, tgws_state = result
            self._stop_pick_timer()
            self.pick_button.set_sensitive(True)
            self.pick_button.set_label(_("Подобрать стратегию"))
            self.cancel_button.set_visible(False)
            self._refresh_strategy_info()
            common.toast(
                self.app,
                _("Отменено. Zapret2: {zap}, TgWsProxy: {tg}").format(
                    zap=zapret_state, tg=tgws_state
                ),
            )

        def failed(exc: Exception) -> None:
            common.message(self.app, _("Ошибка при отмене подбора: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def _refresh_strategy_info(self) -> None:
        """Показать текущую стратегию + результаты подбора (из последнего прогона)."""

        def job():
            profiles = zapret.read_profiles()
            # Показываем профиль, который nfqws2 реально выберет первым для
            # домена из hostlist (отладочный лог подтвердил: «голый» rut-
            # профиль перекрывает профиль с <HOSTLIST>).
            effective = zapret.active_profile_for(profiles, "kws2.pclead.co.uk")
            current = " ".join(
                str(t) for t in (effective.get("desync") or [])
            ).strip() if effective else ""
            is_default = bool(effective and effective.get("uses_placeholder"))
            # Только результат текущего запуска: после перезапуска LinFix
            # надпись «Подобрано: ошибка» больше не появляется.
            result = self._pick_result
            best = result.best if result else None
            return (
                current,
                is_default,
                len(result.strategies) if result else 0,
                best.args if best else "",
                result.error or "" if result else "",
            )

        def done(result):
            current, is_default, count, best_args, error = result
            text = _("Текущая: {cur}").format(cur=current or _("не задана"))
            if is_default:
                text += _(" (по умолчанию)")
            if best_args:
                text += "  |  " + _("Подобрано: {n} (лучшая: {best})").format(
                    n=count, best=best_args[:60]
                )
            elif error:
                text += "  |  " + _("Подобрано: ошибка")
            label = self.strat_label
            for cls in ("strategy-best", "strategy-none", "strategy-error"):
                label.get_style_context().remove_class(cls)
            if best_args:
                label.get_style_context().add_class("strategy-best")
            elif error:
                label.get_style_context().add_class("strategy-error")
            elif not current:
                label.get_style_context().add_class("strategy-none")
            label.set_text(text)

        def failed(_exc: Exception) -> None:
            self.strat_label.set_text(_("Текущая: —"))
            for cls in ("strategy-best", "strategy-none", "strategy-error"):
                self.strat_label.get_style_context().remove_class(cls)

        run_in_thread(job, on_done=done, on_error=failed)

    def _load_blockcheck_domains(self, cfg=None) -> list[str]:
        config = cfg or self.app.cfg
        return list(config.blockcheck.effective_domains)

    def pick_strategy(self) -> None:
        """Подобрать рабочую стратегию desync через blockcheck2.

        Пользователь должен понимать: zapret на время замеров остановится.
        """

        from ... import blockcheck

        if blockcheck.scan_in_progress():
            self.cancel_button.set_visible(True)
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
                "Проверяются домены: {domains}. Список задаётся секцией "
                "[blockcheck] domains в /etc/linfix/config.toml.\n\n"
                "Подбор ничего не меняет в конфиге: вы просто увидите список "
                "рабочих стратегий."
            ).format(domains=", ".join(self._load_blockcheck_domains()))
            + ("\n\n" + _("Сейчас: {reason}").format(reason=reason) if not ok else "")
        )
        dialog.add_button(_("Отмена"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Подобрать"), 1)
        dialog.add_button(_("Отмена подбора"), 2)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response == 2:
            self.cancel_button.set_visible(True)
            self._cancel_strategy_selection()
            return
        if response != 1:
            return

        self.pick_button.set_sensitive(False)
        self.cancel_button.set_visible(True)
        # Новый прогон: результат прошлого в этом запуске больше не актуален.
        self._pick_result = None
        self._refresh_strategy_info()
        self.pick_button.set_label(_("⏳ Подбор идёт…"))
        self.verdict_label.set_text(
            _("⏳ Идёт подбор стратегии… ожидаем завершения прогона")
        )
        self._pick_start = time.monotonic()
        self._pick_timer = GLib.timeout_add_seconds(10, self._tick_pick)

        def job():
            return api.pick_strategy(self.app.cfg, level="quick", timeout=2100)

        def done(result) -> None:
            self._stop_pick_timer()
            self.pick_button.set_sensitive(True)
            self.pick_button.set_label(_("Подобрать стратегию"))
            self.cancel_button.set_visible(False)
            code, payload = result
            if code == api.EXIT_OK:
                self._pick_result = blockcheck.result_from_payload(payload)
            else:
                self._pick_result = None
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
                self._refresh_strategy_info()
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
            self._refresh_strategy_info()
            common.toast(
                self.app,
                _("Найдено стратегий: {n}, zapret снова запущен").format(n=len(strategies)),
            )

        def failed(exc: Exception) -> None:
            self._stop_pick_timer()
            self.pick_button.set_sensitive(True)
            self.pick_button.set_label(_("Подобрать стратегию"))
            self.cancel_button.set_visible(False)
            common.message(self.app, _("Ошибка подбора: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def run_network_diag(self) -> None:
        """Запустить новый прогон диагностики и сохранить его в историю."""

        self.net_button.set_sensitive(False)
        self.net_button.set_label(_("⏳ Проверяется…"))
        self.verdict_label.set_text(
            _("⏳ Идёт диагностика Telegram… это занимает до 20 секунд")
        )

        def job():
            return netdiag.run_network_diagnostics(
                proxy_running=bool(tgws.service_state() == "active"),
                zapret_running=bool(zapret.detect_zapret(self.app.cfg.zapret.service).active),
                timeout=2.5,
            )

        def done(report) -> None:
            self.net_button.set_sensitive(True)
            self.net_button.set_label(_("Обновить"))
            # Ошибка записи истории не должна скрывать сам результат.
            try:
                netdiag_history.save(report)
            except Exception as exc:  # noqa: BLE001
                print(f"история диагностики: {exc}", flush=True)
            self._show_report(report)
            self.refresh_netdiag_history()

        def failed(exc: Exception) -> None:
            self.net_button.set_sensitive(True)
            self.net_button.set_label(_("Обновить"))
            self.verdict_label.set_text(_("Ошибка диагностики: {err}").format(err=exc))

        run_in_thread(job, on_done=done, on_error=failed)

    def _show_report(self, report) -> None:
        """Показать вердикт и рекомендации конкретного прогона."""

        emoji, title, detail = report.verdict
        self.verdict_label.set_text(f"{emoji} {title}\n{detail}")
        self.net_hint.set_visible(False)
        self.net_scrolled.set_visible(True)
        self.reco_label.set_text(
            _("Что делать:\n") + "\n".join(f"• {tip}" for tip in report.recommendations)
        )

    def clear_history(self) -> None:
        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Очистить историю диагностики?"),
        )
        confirm.format_secondary_text(
            _("Удалятся все сохранённые прогоны. Сами проверки не пострадают.")
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            netdiag_history.clear()
        except Exception as exc:  # noqa: BLE001
            common.message(self.app, _("Не удалось очистить историю: {err}").format(err=exc))
            return
        self.verdict_label.set_text("")
        self.reco_label.set_text("")
        self.refresh_netdiag_history()

    def refresh_netdiag_history(self) -> None:
        """Показать последние прогоны из базы; тяжёлых проверок не делает."""

        def job():
            return netdiag_history.recent(limit=20)

        def done(entries) -> None:
            self._history = list(entries)
            self.net_store.clear()
            for entry in self._history:
                media = (
                    f"{entry.media_kbps:.0f} кбит/с"
                    if entry.media_kbps > 0
                    else "—"
                )
                self.net_store.append(
                    [
                        entry.created_display,
                        f"{entry.verdict_emoji} {entry.verdict_title}".strip(),
                        "OK" if entry.direct_ok else "BLOCK",
                        "OK" if entry.proxy_ok else "BLOCK",
                        media,
                    ]
                )
            self.net_scrolled.set_visible(bool(self._history))
            self.net_hint.set_visible(not self._history)

        def failed(exc: Exception) -> None:
            self.net_hint.set_visible(True)
            self.net_hint.set_text(
                _("Не удалось прочитать историю: {err}").format(err=exc)
            )

        run_in_thread(job, on_done=done, on_error=failed)

    def _on_history_row(self, _tree, path, _column=None) -> None:
        """Двойной клик по прогону — показать его подробности."""

        try:
            index = int(path.to_string())
        except (AttributeError, ValueError):
            return
        if not (0 <= index < len(self._history)):
            return
        report = self._history[index].report()
        if report is None:
            common.message(self.app, _("Не удалось прочитать сохранённый прогон"))
            return
        self._show_report(report)

    def refresh_profiles(self) -> None:
        """Показать профили zapret: активные и игнорируемые."""

        self._refresh_strategy_info()

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
                is_global = bool(profile.get("bare_hostlists"))
                if is_global:
                    ignored += 1
                strategy = ", ".join(profile.get("desync") or [])[:60]
                self.profile_store.append(
                    [
                        str(profile.get("protocol") or "—"),
                        _("глобально") if is_global else _("работает") if active else _("игнорируется"),
                        str(profile.get("applies_to") or "—"),
                        strategy,
                    ]
                )
            note = _(
                "Профили desync: {n} правил, из них глобальных {global_n}. "
                "«Глобальный» — путь записан без --hostlist=: nfqws2 применяет "
                "такое правило ко всем доменам, и оно перекрывает последующие. "
                "В строке «Текущая» показан именно первый сработавший профиль."
            ).format(n=len(profiles), global_n=ignored)
            self.profile_note.set_text(note)

        run_in_thread(job, on_done=done)

    def refresh(self) -> None:
        def job():
            return api.get_doctor_report(self.app.cfg)

        def apply_report(report):
            self.last_report = report
            self.refresh_banner(report)
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

    # --- баннер висячих IPv6-заглушек ------------------------------------

    def refresh_banner(self, report) -> None:
        """Показать предупреждение, если весь IPv6 уходит в blackhole."""

        check = next(
            (c for c in report.checks if c.id == "ipv6.blackhole"), None
        )
        if check is None or check.status == "pass":
            self._hide_banner()
            return
        if check.status != "warn":
            self._hide_banner()
            return

        # VPN активен — убирать нельзя, баннер только поясняет.
        if "VPN" in (check.hint or "") and "Убрать" not in (check.hint or ""):
            self._show_banner(
                _(
                    "Обнаружены висячие IPv6-заглушки, но VPN ещё работает: "
                    "маршруты нужны ему. Отключите VPN штатно — и раздел "
                    "покажет, что делать дальше."
                ),
                button=False,
            )
            return

        self._show_banner(
            _(
                "Обнаружены висячие IPv6-заглушки. Из-за них весь IPv6 "
                "уходит в никуда: браузер пробует IPv6-адрес, ждёт таймаут "
                "и только потом переходит на IPv4 — сайты кажутся "
                "недоступными. Нажмите «Убрать заглушки»."
            ),
            button=True,
        )

    def _show_banner(self, text: str, *, button: bool) -> None:
        self.banner_label.set_text(text)
        self.banner_button.set_sensitive(button)
        # Баннер создан с set_no_show_all(True): показываем явно.
        self.banner.set_no_show_all(False)
        self.banner.show_all()
        self.banner_buttons.set_visible(button)

    def _hide_banner(self) -> None:
        self.banner.set_visible(False)

    def clear_ipv6_blackholes(self) -> None:
        """Убрать заглушки через helper; показать, что получилось."""

        self.banner_button.set_sensitive(False)
        self.banner_button.set_label(_("⏳ Убираю…"))

        def job():
            return api.clear_ipv6_blackholes()

        def done(result) -> None:
            ok, message = result
            self.banner_button.set_sensitive(True)
            self.banner_button.set_label(_("Убрать заглушки"))
            if ok:
                # Не красный баннер, а спокойное подтверждение.
                self.banner_label.set_text(message)
                self.banner_label.get_style_context().remove_class("banner-error")
                self.banner_label.get_style_context().add_class("banner-ok")
                self.banner.set_no_show_all(False)
                self.banner.show_all()
                self.banner_buttons.set_visible(False)
                common.toast(self.app, message)
                self.refresh()
            else:
                self.banner_label.set_text(message)
                self.banner_label.get_style_context().remove_class("banner-ok")
                self.banner_label.get_style_context().add_class("banner-error")
                self.banner.set_no_show_all(False)
                self.banner.show_all()
                self.banner_buttons.set_visible(True)

        run_in_thread(job, on_done=done, on_error=self._banner_failed)

    def _banner_failed(self, exc: Exception) -> None:
        self.banner_button.set_sensitive(True)
        self.banner_button.set_label(_("Убрать заглушки"))
        self.banner_label.set_text(
            _("Не удалось убрать заглушки: {err}").format(err=exc)
        )
        self.banner_label.get_style_context().remove_class("banner-ok")
        self.banner_label.get_style_context().add_class("banner-error")
        self.banner.set_no_show_all(False)
        self.banner.show_all()

    def copy_report(self) -> None:
        if self.last_report is None:
            common.toast(self.app, _("Отчёт ещё не создан"))
            return
        clipboard = Gtk.Clipboard.get_for_display(
            Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
        )
        clipboard.set_text(self.last_report.to_json(), len(self.last_report.to_json()))
        common.toast(self.app, _("Отчёт скопирован"))
