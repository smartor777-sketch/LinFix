"""Экран «Статус» (SPEC 14).

Карточки: Zapret, TgWsProxy, Домены, Telegram. Кнопка «Обновить».
(Кнопки управления службами и ссылка Telegram — в разделе «Настройки».)
"""

from __future__ import annotations

from gi.repository import Gtk

from ... import api, blockcheck, doctor, tgws, updates, util, zapret
from ..i18n import _
from ..worker import run_in_thread
from . import common


class StatusScreen:
    def __init__(self, app) -> None:
        self.app = app
        self.box = common.screen_box(_("Статус"))
        self.zapret_info = zapret.detect_zapret(app.cfg.zapret.service)
        self.has_zapret = self.zapret_info.found

        self.zapret_card = common.Card(_("Zapret"))
        for key, text in (
            ("service", _("Служба:")),
            ("active", _("Состояние:")),
            ("pid", _("PID:")),
            ("install_dir", _("Установка:")),
            ("qnum", _("Очередь (qnum):")),
            ("fwmark", _("fwmark:")),
            ("user", _("user:")),
            ("hostlist", _("Путь hostlist:")),
            ("cf", _("CF-домены:")),
        ):
            self.zapret_card.add(key, text)

        self.tgws_card = common.Card(_("TgWsProxy"))
        for key, text in (
            ("installed", _("Установлен:")),
            ("version", _("Версия:")),
            ("service", _("Служба:")),
            ("port", _("Порт:")),
        ):
            self.tgws_card.add(key, text)

        self.domains_card = common.Card(_("Домены"))
        for key, text in (
            ("present", _("В hostlist:")),
            ("total", _("Итого:")),
            ("updated", _("Обновлено:")),
        ):
            self.domains_card.add(key, text)

        self.telegram_card = common.Card(_("Telegram"))
        for key, text in (
            ("connections", _("Соединения:")),
            ("status", _("Статус:")),
            ("server", _("Сервер:")),
            ("port", _("Порт:")),
            ("secret", _("Секрет:")),
        ):
            self.telegram_card.add(key, text)

        # --- Баннер доступных обновлений -------------------------------------
        # Сверху, до карточек: обновление — действие, его надо видеть сразу.
        self.update_banner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=8
        )
        self.update_banner.set_no_show_all(True)
        self.update_banner.set_visible(False)
        self.update_label = common.label("", wrap=True, css_class="banner-info")
        self.update_buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.update_zapret_button = common.button(
            _("Обновить zapret2"), lambda _: self.update_zapret()
        )
        self.update_tgws_button = common.button(
            _("Обновить TgWsProxy"), lambda _: self.update_tgws()
        )
        # no_show_all: окно вызывает show_all() при показе и из трея —
        # без этого флага он снова показал бы кнопку, которую мы скрыли.
        self.update_zapret_button.set_no_show_all(True)
        self.update_tgws_button.set_no_show_all(True)
        common.pack(self.update_buttons, self.update_zapret_button)
        common.pack(self.update_buttons, self.update_tgws_button)
        common.pack(self.update_banner, self.update_label)
        common.pack(self.update_banner, self.update_buttons)
        common.pack(self.box, self.update_banner)

        # Кнопка обновления — сверху, до карточек: так её видно без прокрутки.
        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=8)
        common.pack(self.box, self.buttons)

        # Результат проверки — прямо под кнопкой, чтобы ответ был там же,
        # где нажали, а не только внизу экрана. Строка на каждый компонент:
        # так цвет показывает состояние каждого отдельно.
        self.check_result_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_bottom=8
        )
        self.check_result_box.set_no_show_all(True)
        self.check_result_box.set_visible(False)
        common.pack(self.box, self.check_result_box)

        # --- Висячие IPv6-заглушки: видно сразу на «Статусе» ----------------
        # Пока они есть, весь IPv6 уходит в никуда: браузер ждёт таймаут и
        # сайты «не открываются». Поэтому не только в «Диагностике», но и на
        # первом экране, с кнопкой — ровно там, где ищут причину.
        self.blackhole_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=8
        )
        self.blackhole_box.set_no_show_all(True)
        self.blackhole_box.set_visible(False)
        self.blackhole_label = common.label("", wrap=True, css_class="banner-error")
        self.blackhole_button = common.button(
            _("Убрать заглушки"), lambda _: self.clear_blackholes()
        )
        common.pack(self.blackhole_box, self.blackhole_label)
        # Кнопка слева, а не на всю ширину: растянутая кнопка выглядит как
        # панель и не читается как действие.
        blackhole_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        common.pack(blackhole_row, self.blackhole_button)
        common.pack(self.blackhole_box, blackhole_row)
        common.pack(self.box, self.blackhole_box)

        common.pack(self.box, self.zapret_card)
        common.pack(self.box, self.tgws_card)
        common.pack(self.box, self.domains_card)
        common.pack(self.box, self.telegram_card)

        self._build_buttons()
        self.refresh()
        # Фоновая проверка обновлений: раз в сутки, тихо.
        self.maybe_check_updates()

    def _build_buttons(self) -> None:
        common.pack(self.buttons, common.button(_("Обновить"), lambda _: self.refresh()))
        self.check_button = common.button(
            _("Проверить обновления"), lambda _: self.check_updates()
        )
        common.pack(self.buttons, self.check_button)

    def refresh(self) -> None:
        def job():
            status = api.get_status(self.app.cfg)
            info = status.zapret
            blackholes = doctor.ipv6_blackhole_routes()
            vpn = doctor.vpn_interfaces()
            connections = len(zapret.listening_addresses(self.app.cfg.tgws.port))
            telegram_settings = self._telegram_settings()
            if info.active:
                zapret_state = _("active")
            elif blockcheck.running():
                # blockcheck2 штатно останавливает zapret на время замеров —
                # не пугаем пользователя «inactive».
                zapret_state = _("идёт подбор стратегии")
            else:
                zapret_state = _("inactive")
            return {
                "server": telegram_settings["server"],
                "port": telegram_settings["port"],
                "secret": telegram_settings["secret"] or "—",
                "service": info.service or "—",
                "active": zapret_state,
                "pid": str(info.pid or "—"),
                "install_dir": info.install_dir or "—",
                "qnum": str(info.qnum or "—"),
                "fwmark": str(info.fwmark or "—"),
                "user": str(info.user or "—"),
                "hostlist": status.hostlist or "—",
                "cf": f"{status.cf_present}/{status.cf_total}",
                "tgws_installed": _("да") if status.tgws_installed else _("нет"),
                "tgws_version": status.state_info.tgws_version or "—",
                "tgws_service": tgws.service_state() if status.tgws_installed else "—",
                "tgws_port": ", ".join(status.listeners) or _("не слушается"),
                "domains_present": f"{len(status.state_info.domains or ())}/20",
                "domains_total": "20",
                "domains_updated": status.state_info.domains_updated_at or _("никогда"),
                "connections": str(connections),
                "telegram_status": _("active") if connections else _("inactive"),
                "blackholes": blackholes,
                "vpn": vpn,
            }

        def apply_data(data):
            self.zapret_card.set("service", data["service"])
            self.zapret_card.set("active", data["active"])
            self.zapret_card.set("pid", data["pid"])
            self.zapret_card.set("install_dir", data["install_dir"])
            self.zapret_card.set("qnum", data["qnum"])
            self.zapret_card.set("fwmark", data["fwmark"])
            self.zapret_card.set("user", data["user"])
            self.zapret_card.set("hostlist", data["hostlist"])
            self.zapret_card.set("cf", data["cf"])
            self.tgws_card.set("installed", data["tgws_installed"])
            self.tgws_card.set("version", data["tgws_version"])
            self.tgws_card.set("service", data["tgws_service"])
            self.tgws_card.set("port", data["tgws_port"])
            self.domains_card.set("present", data["domains_present"])
            self.domains_card.set("total", data["domains_total"])
            self.domains_card.set("updated", data["domains_updated"])
            self.telegram_card.set("connections", data["connections"])
            self.telegram_card.set("status", data["telegram_status"])
            self.telegram_card.set("server", data["server"])
            self.telegram_card.set("port", data["port"])
            self.telegram_card.set("secret", data["secret"])
            self._show_blackholes(data["blackholes"], data["vpn"])

        run_in_thread(job, on_done=apply_data)

    # --- обновления -------------------------------------------------------

    def check_updates(self, *, quiet: bool = False) -> None:
        """Проверить новые версии и показать баннер, если что-то есть.

        quiet=True — фоновая суточная проверка: молчим, если обновлений нет.
        """

        if not quiet:
            self.check_button.set_sensitive(False)
            self.check_button.set_label(_("⏳ Проверяю…"))
        # Результат на этот момент неизвестен — не показываем старый кэш
        # как свежий ответ.
        self._show_checking()

        def job():
            return api.check_updates()

        def done(found) -> None:
            if not quiet:
                self.check_button.set_sensitive(True)
                self.check_button.set_label(_("Проверить обновления"))
            self.apply_updates(found, quiet=quiet)

        def failed(exc: Exception) -> None:
            if not quiet:
                self.check_button.set_sensitive(True)
                self.check_button.set_label(_("Проверить обновления"))
                common.toast(self.app, _("Не удалось проверить обновления: {err}").format(err=exc))

            # Сеть или лимит GitHub — показываем последний известный
            # результат, но явно помечаем, что проверить сейчас не вышло.
            cached = updates.load_found()
            if cached.report_lines():
                self.apply_updates(cached, quiet=True)
                line = common.label(
                    _("Не удалось проверить сейчас: {err}").format(err=exc),
                    wrap=True,
                    css_class="check-new",
                )
                self.check_result_box.pack_start(line, False, False, 0)
                self.check_result_box.show_all()

        run_in_thread(job, on_done=done, on_error=failed)

    def apply_updates(self, found, *, quiet: bool = False) -> None:
        """Показать найденные обновления или скрыть баннер."""

        self._show_check_result(found)

        if not found.any:
            self.update_banner.set_visible(False)
            if not quiet:
                common.toast(self.app, _("Обновлений нет"))
            return

        lines: list[str] = []
        if found.has_zapret:
            lines.append(
                _("zapret2: установлена сборка от {cur}, доступна {new}").format(
                    cur=found.zapret_current or "—", new=found.zapret_new
                )
            )
        if found.has_tgws:
            lines.append(
                _("TgWsProxy: установлен {cur}, доступен {new}").format(
                    cur=found.tgws_current or "—", new=found.tgws_new
                )
            )
        lines.append(
            _("Перед обновлением zapret2 сохраняются профили CF и ваши списки, "
              "после установки они возвращаются на место.")
        )
        self.update_label.set_text("\n".join(lines))
        # show_all() показывает всех детей, поэтому кнопки скрываем ПОСЛЕ него.
        self.update_banner.set_no_show_all(False)
        self.update_banner.show_all()
        self.update_zapret_button.set_visible(found.has_zapret)
        self.update_tgws_button.set_visible(found.has_tgws)
        self.update_buttons.set_visible(found.has_zapret or found.has_tgws)

    def _show_checking(self) -> None:
        """Пока идёт проверка — честно пишем, что результат ещё неизвестен."""

        for child in self.check_result_box.get_children():
            self.check_result_box.remove(child)
            child.destroy()
        line = common.label(_("⏳ Проверяю обновления…"), wrap=True)
        self.check_result_box.pack_start(line, False, False, 0)
        self.check_result_box.set_no_show_all(False)
        self.check_result_box.show_all()

    def _show_check_result(self, found) -> None:
        """Отчёт под кнопкой: по каждому компоненту, есть ли обновление.

        Каждая строка красится отдельно: зелёная — обновлять нечего,
        оранжевая — есть новое. Так видно состояние каждого компонента,
        а не общий вывод «обновлений нет».
        """

        for child in self.check_result_box.get_children():
            self.check_result_box.remove(child)
            child.destroy()

        lines = found.report_lines()
        if not lines:
            self.check_result_box.set_visible(False)
            return

        stamp = found.checked_display()
        if stamp:
            # Проверка была не сейчас: честно указываем время, иначе строки
            # выглядят как только что полученный результат.
            header = common.label(
                _("Проверено {when}").format(when=stamp), wrap=True
            )
            header.get_style_context().add_class("check-time")
            self.check_result_box.pack_start(header, False, False, 0)

        for text, has_update in lines:
            line = common.label(
                text, wrap=True, css_class="check-new" if has_update else "check-ok"
            )
            self.check_result_box.pack_start(line, False, False, 0)

        self.check_result_box.set_no_show_all(False)
        self.check_result_box.show_all()

    def _disable_update_buttons(self) -> None:
        self.update_zapret_button.set_sensitive(False)
        self.update_tgws_button.set_sensitive(False)

    def _enable_update_buttons(self) -> None:
        self.update_zapret_button.set_sensitive(True)
        self.update_tgws_button.set_sensitive(True)

    def update_zapret(self) -> None:
        """Обновить zapret2 с сохранением и восстановлением профилей."""

        self._disable_update_buttons()
        self.update_label.set_text(
            _("⏳ Обновляю zapret2: сохраняю профили, ставлю новую сборку, "
              "возвращаю конфиг. Это может занять несколько минут.")
        )

        def job():
            return api.update_zapret2(self.app.cfg)

        def done(result) -> None:
            code, payload = result
            self._enable_update_buttons()
            if code == api.EXIT_OK and payload.get("ok"):
                common.toast(self.app, _("zapret2 обновлён, профили на месте"))
                self.update_banner.set_visible(False)
                self.check_updates(quiet=True)
                self.refresh()
            else:
                self.update_label.set_text(
                    _("Не удалось обновить zapret2: {err}").format(
                        err=payload.get("error") or f"код {code}"
                    )
                )

        def failed(exc: Exception) -> None:
            self._enable_update_buttons()
            self.update_label.set_text(
                _("Не удалось обновить zapret2: {err}").format(err=exc)
            )

        run_in_thread(job, on_done=done, on_error=failed)

    def update_tgws(self) -> None:
        """Обновить TgWsProxy (служба перезапускается автоматически)."""

        self._disable_update_buttons()
        self.update_label.set_text(_("⏳ Обновляю TgWsProxy…"))

        def job():
            return api.install_tgws(self.app.cfg)

        def done(result) -> None:
            code, payload = result
            self._enable_update_buttons()
            if code == api.EXIT_OK and payload.get("ok", True):
                common.toast(
                    self.app,
                    _("TgWsProxy {v}: установлен").format(v=payload.get("version") or ""),
                )
                self.update_banner.set_visible(False)
                self.check_updates(quiet=True)
                self.refresh()
            else:
                self.update_label.set_text(
                    _("Не удалось обновить TgWsProxy: {err}").format(
                        err=payload.get("error") or f"код {code}"
                    )
                )

        def failed(exc: Exception) -> None:
            self._enable_update_buttons()
            self.update_label.set_text(
                _("Не удалось обновить TgWsProxy: {err}").format(err=exc)
            )

    def maybe_check_updates(self) -> None:
        """Проверка обновлений при открытии экрана.

        Проверяем по-настоящему: на момент запуска версии ещё неизвестны, и
        показывать «обновлений нет» из кэша было бы обманом. Один запрос к
        GitHub на запуск — незначительная нагрузка, зато ответ честный.
        """

        self.check_updates(quiet=True)

    def _show_blackholes(self, routes: list[str], vpn: list[str]) -> None:
        """Показать предупреждение о висячих IPv6-заглушках."""

        if not routes:
            self.blackhole_box.set_visible(False)
            return

        if vpn:
            self.blackhole_label.set_text(
                _(
                    "Обнаружены висячие IPv6-заглушки, но VPN ещё работает "
                    "({vpn}): маршруты нужны ему. Отключите VPN штатно."
                ).format(vpn=", ".join(vpn))
            )
            self.blackhole_button.set_visible(False)
        else:
            self.blackhole_label.set_text(
                _(
                    "Обнаружены висячие IPv6-заглушки ({n}). Из-за них весь "
                    "IPv6 уходит в никуда: браузер ждёт таймаут и сайты "
                    "кажутся недоступными."
                ).format(n=len(routes))
            )
            self.blackhole_button.set_visible(True)
            self.blackhole_button.set_sensitive(True)
            self.blackhole_button.set_label(_("Убрать заглушки"))

        self.blackhole_box.set_no_show_all(False)
        self.blackhole_box.show_all()
        if vpn:
            self.blackhole_button.set_visible(False)

    def clear_blackholes(self) -> None:
        """Убрать заглушки через helper и обновить экран."""

        self.blackhole_button.set_sensitive(False)
        self.blackhole_button.set_label(_("⏳ Убираю…"))

        def job():
            return api.clear_ipv6_blackholes()

        def done(result) -> None:
            ok, message = result
            self.blackhole_button.set_sensitive(True)
            self.blackhole_button.set_label(_("Убрать заглушки"))
            common.toast(self.app, message)
            if ok:
                self.blackhole_box.set_visible(False)
            self.refresh()

        def failed(exc: Exception) -> None:
            self.blackhole_button.set_sensitive(True)
            self.blackhole_button.set_label(_("Убрать заглушки"))
            self.blackhole_label.set_text(
                _("Не удалось убрать заглушки: {err}").format(err=exc)
            )

        run_in_thread(job, on_done=done, on_error=failed)

    def _telegram_settings(self) -> dict[str, str]:
        path = tgws.find_config_path()
        data = tgws.load_json_config(path) if path is not None else {}
        return {
            "server": str(data.get("host", "127.0.0.1")),
            "port": str(data.get("port", 1080)),
            "secret": str(data.get("secret", "")),
        }
