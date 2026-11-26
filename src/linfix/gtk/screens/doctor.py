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
        self.strategy_title = common.label(
            _("Стратегии (desync)"), css_class="section-title"
        )
        self.strategy_caption = common.label(
            _(
                "Стратегия (desync) — способ, которым zapret обрабатывает "
                "первые пакеты TLS-соединения, чтобы провайдер не распознал "
                "домен и не заблокировал его.\n"
                "Подбор идёт в три шага. Сначала blockcheck2 перебирает "
                "варианты маскировки (fake, multisplit, multidisorder и их "
                "комбинации) на выбранных доменах — zapret на это время "
                "останавливается. Найденное появляется в таблице внизу.\n"
                "Затем «Проверить найденное» применяет кандидатов по одному и "
                "проверяет телом 256 КБ: одноразовый запрос blockcheck2 "
                "показывает лишь «соединение открылось», а медиа может не "
                "работать.\n"
                "«Применить выбранную» оставляет стратегию в конфиге, "
                "сохранив прежний в резервную копию.\n"
                "Время подбора задаётся полем «секунд» рядом с кнопкой."
            ),
            wrap=True,
        )
        # Таблица найденных стратегий: выбор строки и применение — кнопкой.
        # Текстовый список не давал ни выделить, ни скопировать строку.
        self.found_store = Gtk.ListStore(str, str, str, str)
        self.found_tree = common.table_from_store(
            self.found_store,
            [_("Оценка"), _("Тело 256 КБ"), _("Стратегия"), _("Статус")],
            tooltip_column=2,
        )
        # Без ScrolledWindow: TreeView сам запрашивает высоту по числу строк,
        # внешняя прокрутка страницы уже есть. В обёртке таблица схлопывалась
        # до 33 px и выглядела пустой.
        self.found_scrolled = self.found_tree
        self.found_tree.set_size_request(-1, 120)
        self.found_scrolled.set_no_show_all(True)
        self.found_scrolled.set_visible(False)
        self.found_hint = common.label(
            _("Нажмите «Подобрать стратегию», чтобы увидеть варианты"), wrap=True
        )

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        self.pick_button = common.button(
            _("Подобрать стратегию"), lambda _: self.pick_strategy()
        )
        common.pack(self.buttons, self.pick_button)
        # Сколько секунд дать подбору. По умолчанию из конфига.
        self.pick_seconds = common.entry(str(self.app.cfg.blockcheck.timeout))
        self.pick_seconds.set_width_chars(5)
        self.pick_seconds.set_max_length(5)
        self.pick_seconds.set_tooltip_text(
            _("Сколько секунд вести подбор (по умолчанию 180)")
        )
        common.pack(self.buttons, common.label(_("секунд:")))
        common.pack(self.buttons, self.pick_seconds)
        # Кнопка «отменить и восстановить» — видна во время прогона подбора.
        self.cancel_button = common.button(
            _("Отмена подбора"), lambda _: self._cancel_strategy_selection()
        )
        self.cancel_button.set_visible(False)
        common.pack(self.buttons, self.cancel_button)
        # Проверка качества найденного: одноразовый GET в blockcheck2
        # пропускает стратегии, которые открывают заголовки, но ломают тело.
        self.verify_button = common.button(
            _("Проверить найденное"), lambda _: self.verify_candidates()
        )
        self.verify_button.set_sensitive(False)
        self.verify_button.set_no_show_all(True)
        self.verify_button.set_visible(False)
        common.pack(self.buttons, self.verify_button)
        # Применение выбранной стратегии — с бэкапом профилей.
        self.apply_button = common.button(
            _("Применить выбранную"), lambda _: self.apply_best()
        )
        self.apply_button.set_sensitive(False)
        self.apply_button.set_no_show_all(True)
        self.apply_button.set_visible(False)
        common.pack(self.buttons, self.apply_button)
        self.copy_strategy_button = common.button(
            _("Копировать стратегию"), lambda _: self.copy_selected_strategy()
        )
        common.pack(self.buttons, self.copy_strategy_button)
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
        self.profile_title = common.label(
            _("Профили desync"), css_class="section-title"
        )
        self.profile_title.set_margin_top(8)
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

        common.pack(self.box, self.strategy_title)
        common.pack(self.box, self.strategy_caption)
        common.pack(self.box, self.buttons)
        common.pack(self.box, self.found_hint)
        common.pack(self.box, self.found_scrolled)
        # Отдельный label для подбора: `verdict_label` принадлежит диагностике
        # Telegram, и писать в него результаты подбора нельзя — они перемешивались.
        self.pick_status = common.label("", wrap=True)
        common.pack(self.box, self.pick_status)
        # Краткая информация по стратегиям — отдельной строкой под кнопками,
        # а не между «Подобрать стратегию» и «Отмена подбора».
        self.current_title = common.label(_("Текущая:"), css_class="section-title")
        self.strat_label = common.label("—", css_class="strategy-info")
        self.strat_label.set_margin_bottom(2)
        common.pack(self.box, self.current_title)
        common.pack(self.box, self.strat_label)

        common.pack(self.box, self.profile_title)
        common.pack(self.box, self.profile_note)
        common.pack(self.box, self.profile_scrolled)
        common.pack(self.box, self.scrolled)
        self.last_report = None
        # Результат подбора живёт только в памяти этого запуска GUI:
        # старые ошибки blockcheck не должны видеть при следующем старте.
        self._pick_result: blockcheck.BlockcheckResult | None = None
        self._verified: list[dict] = []
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
        elapsed = int(time.monotonic() - base)
        budget = getattr(self, "_pick_budget", 0) or 0
        # Каждые 5 секунд обновляем счётчик: подбор идёт минутами, и без
        # движения текста он неотличим от зависания.
        text = _("⏳ Идёт подбор стратегии… прошло {s} с").format(s=elapsed)
        if budget:
            text += _(" из {b} с").format(b=budget)
        # Число кандидатов во время прогона недоступно: helper отдаёт
        # результат только по завершении. Поэтому показываем прогресс по
        # времени и честную подсказку, а не выдуманный счётчик.
        text += _(
            "\nЭто нормально: blockcheck2 проверяет варианты по порядку, "
            "а найденное соберётся к концу."
        )
        self.pick_status.set_text(text)
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
            text = current or _("не задана")
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
            self.strat_label.set_text("—")
            for cls in ("strategy-best", "strategy-none", "strategy-error"):
                self.strat_label.get_style_context().remove_class(cls)

        run_in_thread(job, on_done=done, on_error=failed)

    def _load_blockcheck_domains(self, cfg=None) -> list[str]:
        config = cfg or self.app.cfg
        return list(config.blockcheck.effective_domains)

    def _pick_seconds_value(self) -> int:
        """Сколько секунд вести подбор: из поля, иначе из конфига.

        Мусор в поле не должен ломать подбор — тогда берём значение
        по умолчанию и ограничиваем разумным диапазоном.
        """

        default = self.app.cfg.blockcheck.timeout
        raw = self.pick_seconds.get_text().strip()
        try:
            value = int(raw) if raw else default
        except ValueError:
            value = default
        return max(60, min(value, 2100))

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
                "Запустится штатный blockcheck2.sh из zapret и будет работать "
                "{seconds} секунд — столько указано в поле «секунд».\n\n"
                "zapret на время замеров будет ОСТАНОВЛЕН и включён обратно: "
                "иначе он вмешивается в замеры. Без обхода на это время "
                "останутся только сайты, которые и так открываются.\n\n"
                "Проверяются домены: {domains}. Список задаётся секцией "
                "[blockcheck] domains в /etc/linfix/config.toml.\n\n"
                "Что будет дальше:\n"
                "• найденные варианты появятся в таблице под кнопками;\n"
                "• «Проверить найденное» отсеет те, что ломают медиа;\n"
                "• «Применить выбранную» оставит стратегию в конфиге, "
                "сохранив прежний в резервную копию.\n\n"
                "Сам подбор конфиг не меняет."
            ).format(
                seconds=self._pick_seconds_value(),
                domains=", ".join(self._load_blockcheck_domains()),
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
            self.cancel_button.set_visible(True)
            self._cancel_strategy_selection()
            return
        if response != 1:
            return

        self.pick_button.set_sensitive(False)
        self.cancel_button.set_visible(True)
        # Новый прогон: результат прошлого в этом запуске больше не актуален.
        self._pick_result = None
        self._verified = []
        self.verify_button.set_no_show_all(True)
        self.verify_button.set_visible(False)
        self.apply_button.set_no_show_all(True)
        self.apply_button.set_visible(False)
        self._refresh_strategy_info()
        self.pick_button.set_label(_("⏳ Подбор идёт…"))
        self.pick_status.set_text(_("⏳ Идёт подбор стратегии…"))
        self._pick_start = time.monotonic()
        self._pick_timer = GLib.timeout_add_seconds(5, self._tick_pick)

        cfg = self.app.cfg
        level = cfg.blockcheck.level
        # Секунды берём из поля рядом с кнопкой: пользователь может дать
        # больше времени на медленной сети.
        budget = self._pick_seconds_value()
        # Прогресс по времени: подбор идёт минутами, и без счётчика он
        # выглядит как зависание.
        self._pick_budget = budget

        def job():
            return api.pick_strategy(cfg, level=level, timeout=budget)

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
                self.pick_status.set_text(
                    _("Прогон не завершён — проверены не все варианты")
                    if timed_out
                    else _("Стратегии не нужны: {note}").format(note=message)
                )
                self._refresh_strategy_info()
                return
            self._fill_found_table(strategies, best)
            lines = [
                _("Найдено стратегий: {n}").format(n=len(strategies)),
                _("Выберите строку в таблице и нажмите «Применить выбранную»."),
                "",
            ]
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
            self.pick_status.set_text("\n".join(lines))
            # Появился кандидат — можно проверить его качество.
            self.verify_button.set_no_show_all(False)
            self.verify_button.set_visible(True)
            self.verify_button.set_sensitive(True)
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

    # --- проверка и применение найденной стратегии ------------------------

    def _fill_found_table(self, strategies: list, best) -> None:
        """Показать всех найденных в таблице: выбор, копирование, применение.

        Принимает и `Strategy` (из подбора), и словари (после проверки):
        внутри всё приводится к словарю с `args`.
        """

        def as_dict(item) -> dict:
            if isinstance(item, dict):
                return item
            return {
                "args": getattr(item, "args", ""),
                "success": getattr(item, "success", 0),
                "total": getattr(item, "total", 0),
                "desync": list(getattr(item, "desync", []) or []),
            }

        self._found = [as_dict(item) for item in strategies]
        best_args = ""
        if best is not None:
            best_args = str(best.get("args") if isinstance(best, dict) else getattr(best, "args", ""))
        self.found_store.clear()
        for item in self._found:
            mark = "★" if best_args and item["args"] == best_args else ""
            self.found_store.append(
                [
                    f"{mark}{item.get('success', 0)}/{item.get('total', 0)}",
                    "—",
                    item["args"],
                    _("лучшая") if mark else "",
                ]
            )
        self.found_scrolled.set_no_show_all(False)
        self.found_scrolled.set_visible(True)
        self.found_hint.set_visible(False)
        # Первая строка выбрана: сразу видно, что можно применять.
        if len(self.found_store):
            self.found_tree.get_selection().select_path(Gtk.TreePath.new_first())

    def _selected_strategy(self) -> dict | None:
        model, tree_iter = self.found_tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        path = model.get_path(tree_iter)
        index = path.get_indices()[0]
        if 0 <= index < len(getattr(self, "_found", [])):
            return self._found[index]
        return None

    def copy_selected_strategy(self) -> None:
        """Скопировать строку стратегии целиком — она длинная и в окне обрезана."""

        item = self._selected_strategy()
        if item is None:
            common.toast(self.app, _("Выберите строку в таблице"))
            return
        text = str(item.get("args") or "")
        if not text:
            common.toast(self.app, _("Пустая строка"))
            return
        clipboard = Gtk.Clipboard.get_for_display(
            Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD
        )
        # Длина для clipboard — в байтах, иначе кириллица обрезается.
        clipboard.set_text(text, len(text.encode("utf-8")))
        common.toast(self.app, _("Стратегия скопирована"))

    def verify_candidates(self) -> None:
        """Проверить найденных кандидатов телом 256 КБ с реальным применением.

        Одноразовый GET в blockcheck2 говорит лишь «соединение открылось».
        Здесь каждый кандидат применяется к zapret, проверяется H2-телом и
        откатывается — только так видно поломку медиа.
        """

        if not self._pick_result or not self._pick_result.strategies:
            common.toast(self.app, _("Сначала подберите стратегии"))
            return

        strategies = [
            {"args": item.args, "desync": item.desync}
            for item in self._pick_result.strategies[:4]
        ]

        self.verify_button.set_sensitive(False)
        self.verify_button.set_label(_("⏳ Проверяю…"))
        self.pick_status.set_text(
            _(
                "⏳ Проверяю кандидатов телом 256 КБ: каждый применяется к "
                "zapret по очереди, затем профиль возвращается. Это занимает "
                "около 10 секунд на кандидата."
            )
        )

        def job():
            return api.verify_candidates_via_helper(self.app.cfg, strategies, limit=4)

        def done(result) -> None:
            self.verify_button.set_sensitive(True)
            self.verify_button.set_label(_("Проверить найденное"))
            code, payload = result
            checked = payload.get("checked") or []
            if code != api.EXIT_OK or not checked:
                self.pick_status.set_text(
                    _("Не удалось проверить: {err}").format(
                        err=payload.get("error") or "нет результата"
                    )
                )
                return

            self._verified = checked
            best = payload.get("best")
            lines = [_("Проверка телом 256 КБ:"), ""]
            for item in checked:
                ok = item.get("body_ok")
                count = item.get("body_ok_count")
                total = item.get("body_total")
                mark = "✅" if ok else "❌"
                detail = (
                    f"{count}/{total}" if count is not None else "не проверен"
                )
                lines.append(f"{mark} {detail}  {str(item.get('desync'))[:90]}")
            lines.append("")
            if best:
                lines.append(
                    _("Лучшая по медиа выбрана — можно применить её кнопкой ниже.")
                )
                self.apply_button.set_no_show_all(False)
                self.apply_button.set_visible(True)
                self.apply_button.set_sensitive(True)
            else:
                lines.append(
                    _(
                        "Ни одна не прошла тело целиком: у вашего провайдера они "
                        "открывают заголовки, но ломают передачу. Это ровно тот "
                        "случай, когда одноразовый GET обманывает."
                    )
                )
                self.apply_button.set_visible(False)
            self.pick_status.set_text("\n".join(lines))

        def failed(exc: Exception) -> None:
            self.verify_button.set_sensitive(True)
            self.verify_button.set_label(_("Проверить найденное"))
            self.pick_status.set_text(
                _("Ошибка проверки: {err}").format(err=exc)
            )

        run_in_thread(job, on_done=done, on_error=failed)

    def apply_best(self) -> None:
        """Применить выбранную стратегию к zapret с бэкапом профилей.

        Выбор берётся из таблицы. Если в таблице отмечена проверенная
        строка — берём её desync. Иначе разбираем строку стратегии как есть
        (пользователь мог выбрать непроверенный вариант осознанно).
        """

        chosen = self._selected_strategy()
        candidates = getattr(self, "_verified", []) or []

        best = None
        if chosen is not None:
            args = str(chosen.get("args") or "")
            # Ищем совпадение среди проверенных: там есть готовый desync.
            for item in candidates:
                if str(item.get("args") or "") == args:
                    best = item
                    break
            if best is None:
                desync = blockcheck.normalize_args(args)
                if desync:
                    best = {"args": args, "desync": desync.split(), "body_ok": None}
        if best is None:
            for item in candidates:
                if item.get("body_ok"):
                    best = item
                    break
        if not best:
            common.toast(self.app, _("Выберите стратегию в таблице"))
            return

        confirm = Gtk.MessageDialog(
            transient_for=self.app.window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Применить эту стратегию к zapret?"),
        )
        confirm.format_secondary_text(
            _(
                "Профиль добавится первым правилом для вашего hostlist, zapret "
                "перезапустится. Текущий конфиг сохранится в резервную копию.\n\n"
                "{desync}{warn}"
            ).format(
                desync=" ".join(best.get("desync") or [])[:300],
                warn=(
                    ""
                    if best.get("body_ok")
                    else "\n\nВНИМАНИЕ: эта стратегия не проверена телом 256 КБ."
                ),
            )
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return

        self.apply_button.set_sensitive(False)
        self.apply_button.set_label(_("⏳ Применяю…"))

        def job():
            return api.apply_strategy_via_helper(self.app.cfg, best.get("desync") or [])

        def done(result) -> None:
            self.apply_button.set_sensitive(True)
            self.apply_button.set_label(_("Применить выбранную"))
            code, payload = result
            if code == api.EXIT_OK and payload.get("ok"):
                common.message(
                    self.app,
                    _(
                        "Стратегия применена.\n\nБэкап конфига: {backup}\n\n"
                        "Откатить: верните файл из бэкапа и перезапустите zapret."
                    ).format(backup=payload.get("backup") or "—"),
                )
                self.refresh_profiles()
            else:
                common.message(
                    self.app,
                    _("Не удалось применить: {err}").format(
                        err=payload.get("error") or f"код {code}"
                    ),
                )

        def failed(exc: Exception) -> None:
            self.apply_button.set_sensitive(True)
            self.apply_button.set_label(_("Применить выбранную"))
            common.message(self.app, _("Ошибка применения: {err}").format(err=exc))

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
        # WARN теперь означает именно висячие заглушки без активного VPN.
        # Если VPN работает, проверка возвращает PASS: маршруты нужны ему.
        if check is None or check.status != "warn":
            self._hide_banner()
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
        payload = self.last_report.to_json()
        clipboard.set_text(payload, len(payload.encode("utf-8")))
        common.toast(self.app, _("Отчёт скопирован"))
