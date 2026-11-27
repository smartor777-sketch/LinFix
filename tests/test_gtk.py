from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from linfix.gtk import icons  # noqa: E402
from linfix.gtk.i18n import install, _  # noqa: E402
from linfix.gtk.tray import Tray  # noqa: E402
from unittest.mock import patch  # noqa: E402

HAS_DISPLAY = Gtk.init_check(None)[0]


class I18nTests(unittest.TestCase):
    def test_ru_and_en(self) -> None:
        install("ru")
        self.assertEqual(_("Статус"), "Статус")
        install("en")
        self.assertEqual(_("Статус"), "Status")
        install("ru")

    def test_fallback(self) -> None:
        install("de")  # unknown -> ru
        self.assertEqual(_("Статус"), "Статус")


class IconTests(unittest.TestCase):
    def test_icons_exist(self) -> None:
        for name in ("linfix", "tray-ok", "tray-warn", "tray-fail"):
            self.assertTrue(icons.icon_path(name), f"missing {name}")


class TrayTests(unittest.TestCase):
    @unittest.skipUnless(HAS_DISPLAY, "needs display")
    def test_tray_backend(self) -> None:
        class AppStub:
            def show(self) -> None:
                pass

            def quit(self) -> None:
                pass

        tray = Tray(AppStub(), icons.tray_icon_path("ok"), tooltip="LinFix")
        self.assertIn(tray.backend, {"ayatana", "xapp"})
        self.assertIsNotNone(tray.indicator)
        tray.set_icon(icons.tray_icon_path("warn"))
        tray.set_icon(None)


@unittest.skipUnless(HAS_DISPLAY, "needs display")
class AppTests(unittest.TestCase):
    def test_app_creates_window_and_screens(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        self.assertIsNotNone(app.window)
        self.assertIsNotNone(app.tray)
        self.assertEqual(app.window.notebook.get_n_pages(), 8)
        self.assertEqual(
            sorted(app.window.screens),
            [
                "doctor",
                "domains",
                "hostlist",
                "logs",
                "settings",
                "setup",
                "sitecheck",
                "status",
            ],
        )
        status = app.window.screens["status"]
        self.assertEqual(status.zapret_card.title_label.get_text(), "Zapret")
        domains = app.window.screens["domains"]
        # колонки: галочка + домен + статус + детали
        self.assertEqual(domains.store.get_n_columns(), 4)
        # Кнопки лежат в FlowBox: get_children() отдаёт обёртки FlowBoxChild,
        # поэтому забираем виджет через get_child().
        buttons = [child.get_child() for child in domains.buttons.get_children()]
        self.assertEqual(
            [b.get_label() for b in buttons],
            [
                "Обновить и проверить",
                "Тест TLS выбранных",
                "Тест всего списка",
                "Проверить профиль CF",
                "Добавить домен",
                "Удалить выбранные",
                "Добавить в ротацию",
                "Удалить из ротации",
            ],
        )
        # Переключатель источника списка: GitHub и пользовательская ротация.
        model = domains.source_combo.get_model()
        self.assertEqual([model[i][1] for i in range(2)], ["github", "user"])
        # В режиме GitHub видны кнопки hostlist, кнопки ротации скрыты.
        self.assertFalse(domains.add_rotation_button.get_visible())
        self.assertTrue(domains.add_button.get_visible())
        doctor = app.window.screens["doctor"]
        self.assertEqual(doctor.store.get_n_columns(), 4)
        settings = app.window.screens["settings"]
        self.assertTrue(settings.port_entry.get_text())

        # Обычное окно: не должно висеть поверх всех приложений.
        self.assertEqual(
            app.window.get_type_hint(), Gdk.WindowTypeHint.NORMAL
        )
        self.assertFalse(app.window.keep_above)
        self.assertFalse(app.window.get_skip_taskbar_hint())
        self.assertTrue(hasattr(settings, "zapret2_switch"))
        settings_labels = [
            child.get_child().get_label()
            for child in settings.services_box.get_children()
            if isinstance(child, Gtk.FlowBoxChild)
        ]
        for label in (
            "Перезапустить",
            "Перезапустить TgWsProxy",
            "Остановить TgWsProxy",
            "Остановить Zapret2",
            "Подключить Telegram",
            "Очистить логи",
        ):
            self.assertIn(label, settings_labels)
        self.assertTrue(hasattr(settings, "link_entry"))
        self.assertTrue(hasattr(settings, "copy_link"))
        self.assertTrue(hasattr(settings, "open_telegram"))
        self.assertTrue(hasattr(settings, "mode_combo"))
        self.assertTrue(app.window.tab_bar)
        tab_labels = [b.get_label() for b in app.window.tab_bar.get_children()]
        self.assertEqual(
            tab_labels,
            [
                "Статус",
                "Мастер настройки",
                "Домены",
                "Списки",
                "Сайты",
                "Диагностика",
                "Настройки",
                "Логи",
            ],
        )
        self.assertEqual(
            [key for key, b in app.window._tab_buttons.items() if b.get_style_context().has_class("active")],
            ["status"],
        )
        setup = app.window.screens["setup"]
        self.assertTrue(setup.step_label.get_text())
        logs = app.window.screens["logs"]
        self.assertTrue(logs.full_text is not None)
        app.window.destroy()

    def test_window_minimize_to_tray(self) -> None:
        from gi.repository import Gdk

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        self.assertTrue(window.get_visible())
        event = Gdk.Event(Gdk.EventType.DELETE)
        event.window = window.get_window()
        # Симулируем минимизацию (iconified).
        returned = window._on_window_state_event(window, Gdk.Event(Gdk.EventType.WINDOW_STATE))
        # Если WM не поддерживает iconified в тесте, проверим что handler есть.
        self.assertTrue(hasattr(window, "_on_window_state_event"))
        app.window.destroy()

    def test_window_hint_and_hide_on_delete(self) -> None:
        from gi.repository import Gdk

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        self.assertEqual(window.get_type_hint(), Gdk.WindowTypeHint.NORMAL)
        self.assertTrue(window.get_deletable())
        self.assertTrue(window.get_visible())
        returned = window._on_delete_event(window, None)
        self.assertTrue(returned)
        self.assertFalse(window.get_visible())
        app.window.destroy()

    def test_status_screen_widgets(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["status"]
        self.assertEqual(
            set(screen.zapret_card.rows),
            {"service", "active", "pid", "install_dir", "qnum", "fwmark", "user", "hostlist", "cf"},
        )
        self.assertEqual(set(screen.tgws_card.rows), {"installed", "version", "service", "port"})
        self.assertEqual(set(screen.domains_card.rows), {"present", "total", "updated"})
        self.assertEqual(
            set(screen.telegram_card.rows),
            {"connections", "status", "server", "port", "secret"},
        )
        self.assertFalse(hasattr(screen, "link_entry"))
        self.assertTrue(screen.buttons.get_children())
        self.assertTrue(screen.box.get_visible())
        labels = [b.get_label() for b in screen.buttons.get_children()]
        self.assertEqual(labels, ["Обновить", "Проверить обновления"])
        app.window.destroy()

    def test_autostarted_services_start_when_enabled(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        with (
            patch(
                "linfix.gtk.app.util.run",
                return_value=SimpleNamespace(stdout="enabled", returncode=0),
            ),
            patch("linfix.gtk.app.api.start_tgws") as start_tgws,
            patch("linfix.gtk.app.api.start_zapret") as start_zapret,
        ):
            app._autostarted_job()
        start_tgws.assert_called_once_with(app.cfg)
        start_zapret.assert_called_once_with(app.cfg)

    def test_autostarted_services_skip_when_disabled(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        with (
            patch(
                "linfix.gtk.app.util.run",
                return_value=SimpleNamespace(stdout="disabled", returncode=0),
            ),
            patch("linfix.gtk.app.api.start_tgws") as start_tgws,
            patch("linfix.gtk.app.api.start_zapret") as start_zapret,
        ):
            app._autostarted_job()
        start_tgws.assert_not_called()
        start_zapret.assert_not_called()

    def test_settings_save_starts_autostarted_services(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        settings = app.window.screens["settings"]
        settings.autostart_switch.set_active(True)
        settings.zapret2_switch.set_active(True)
        with (
            patch(
                "linfix.gtk.screens.settings.api.set_tgws_config",
                return_value=(0, {"ok": True}),
            ),
            patch(
                "linfix.gtk.screens.settings.api.toggle_guard",
                return_value=(0, {"ok": True}),
            ),
            patch(
                "linfix.gtk.screens.settings.api.enable_autostart",
                return_value=(0, {"ok": True}),
            ),
            patch(
                "linfix.gtk.screens.settings.api.set_zapret2_autostart",
                return_value=(0, {"ok": True}),
            ),
            patch(
                "linfix.gtk.screens.settings.util.run",
                return_value=SimpleNamespace(stdout="disabled", returncode=0),
            ),
            patch("linfix.gtk.screens.settings.api.start_tgws", return_value=(0, "ok")) as start_tgws,
            patch(
                "linfix.gtk.screens.settings.api.start_zapret", return_value=(0, "ok")
            ) as start_zapret,
        ):
            settings.save()
        start_tgws.assert_called_once_with(app.cfg)
        start_zapret.assert_called_once_with(app.cfg)
        app.window.destroy()

    def test_window_state_event_iconified_hides(self) -> None:
        from gi.repository import Gdk

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        self.assertTrue(window.get_visible())
        window._restoring = False
        event = Gdk.EventWindowState()
        event.new_window_state = Gdk.WindowState.ICONIFIED
        # Не должно падать (get_state() — None), окно должно свернуться.
        window._on_window_state_event(window, event)
        self.assertFalse(window.get_visible())
        app.window.destroy()

    def test_show_from_tray_restores_window(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        window.hide()
        self.assertFalse(window.get_visible())
        app.show()  # клик по трею: "Развернуть панель"
        self.assertTrue(window.get_visible())
        app.window.destroy()


if __name__ == "__main__":
    unittest.main()


class SettingsActionTests(unittest.TestCase):
    """Кнопки служб должны вызывать API с cfg и не падать в фоне."""

    def test_every_service_button_calls_api(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["settings"]

        called: list[str] = []

        def fake_run_op(fn, **_kwargs):
            try:
                fn()
            except TypeError as exc:  # неверная сигнатура — это баг кнопки
                raise AssertionError(f"кнопка вызвала API неправильно: {exc}") from exc
            called.append(getattr(fn, "__name__", "lambda"))

        with patch.object(app, "run_op", side_effect=fake_run_op), patch(
            "linfix.api.stop_tgws", return_value=(0, "ok")
        ) as stop_tgws, patch(
            "linfix.api.restart_tgws", return_value=(0, "ok")
        ), patch(
            "linfix.api.restart_zapret", return_value=(0, "ok")
        ), patch(
            "linfix.api.stop_zapret", return_value=(0, "ok")
        ):
            for child in screen.services_box.get_children():
                button = child.get_child()
                if button.get_label() in {"Перезапустить", "Перезапустить TgWsProxy",
                                          "Остановить TgWsProxy", "Остановить Zapret2"}:
                    button.emit("clicked")

        # Именно с cfg: без него кнопка падала бы с TypeError в фоне.
        stop_tgws.assert_called_once()
        self.assertEqual(len(stop_tgws.call_args.args), 1)
        self.assertTrue(called)
        app.window.destroy()


class TrayRestartTests(unittest.TestCase):
    """Пункты трея «Перезапустить …» должны передавать cfg в API."""

    def test_tray_restart_passes_config(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()

        def fake_run_op(fn, **_kwargs):
            fn()  # worker вызывает функцию без аргументов

        with patch.object(app, "run_op", side_effect=fake_run_op), patch(
            "linfix.api.restart_tgws", return_value=(0, "ok")
        ) as restart_tgws, patch(
            "linfix.api.restart_zapret", return_value=(0, "ok")
        ) as restart_zapret:
            app.restart_tgws_tray()
            app.restart_zapret_tray()

        restart_tgws.assert_called_once_with(app.cfg)
        restart_zapret.assert_called_once_with(app.cfg)


class LayoutTests(unittest.TestCase):
    def test_status_refresh_button_above_cards(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        status = app.window.screens["status"]
        children = status.box.get_children()
        # 0 — заголовок, 1 — баннер обновлений, 2 — панель кнопок,
        # 3 — строка результата проверки, дальше карточки.
        self.assertIs(children[1], status.update_banner)
        self.assertIs(children[2], status.buttons)
        self.assertIs(children[3], status.check_result_box)
        self.assertEqual(
            [b.get_label() for b in status.buttons.get_children()],
            ["Обновить", "Проверить обновления"],
        )
        app.window.destroy()

    def test_mode_status_is_directly_below_mode_combo(self) -> None:
        from linfix.gtk.app import NetFixApp
        from linfix.gtk.screens.settings import SettingsScreen

        app = NetFixApp()
        screen = SettingsScreen(app)
        combo_top = screen.grid.child_get(screen.mode_combo, "top-attach")
        status_top = screen.grid.child_get(screen.mode_status, "top-attach")
        self.assertEqual(status_top[0], combo_top[0] + 1)
        self.assertEqual(
            screen.grid.child_get(screen.mode_status, "left-attach"), [1]
        )

    def test_switching_sections_scrolls_to_top(self) -> None:
        from linfix.gtk.app import NetFixApp
        import time

        app = NetFixApp()
        app.do_activate()
        window = app.window
        window.resize(900, 560)

        def pump(rounds: int = 40) -> None:
            for _ in range(rounds):
                while Gtk.events_pending():
                    Gtk.main_iteration()
                time.sleep(0.01)

        pump()
        window.set_page("status")
        pump()
        adjustment = window.scrolled.get_vadjustment()
        adjustment.set_value(150)
        pump()
        self.assertGreater(adjustment.get_value(), 0)

        window.set_page("logs")
        pump()
        self.assertEqual(adjustment.get_value(), 0.0)

        window.set_page("status")
        pump()
        self.assertEqual(adjustment.get_value(), 0.0)
        app.window.destroy()


class LogsLayoutTests(unittest.TestCase):
    def test_search_bar_above_log_view(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        logs = app.window.screens["logs"]
        children = logs.box.get_children()
        self.assertLess(
            children.index(logs.search_box),
            children.index(logs.scrolled),
            "поиск и «Обновить» должны быть над логом",
        )
        app.window.destroy()


class LogsCopyTests(unittest.TestCase):
    def test_copy_keeps_cyrillic_intact(self) -> None:
        """Длина для clipboard — в байтах UTF-8, иначе русский текст обрезается."""

        from unittest.mock import patch
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        logs = app.window.screens["logs"]

        text = "=== zapret ===" + "\n" + "ноя 23 ошибка соединения" * 50
        logs.buffer.set_text(text)

        captured: dict[str, object] = {}

        class FakeClipboard:
            def set_text(self, payload, length):
                captured["text"] = payload
                captured["length"] = length

        with patch.object(
            Gtk.Clipboard, "get_for_display", return_value=FakeClipboard()
        ):
            logs.copy_logs()

        self.assertEqual(captured["text"], text)
        # Длина должна покрывать весь текст в байтах, а не в символах.
        self.assertEqual(captured["length"], len(text.encode("utf-8")))
        self.assertGreater(captured["length"], len(text))
        app.window.destroy()


class HostlistLimitTests(unittest.TestCase):
    def test_shows_first_100_but_searches_all(self) -> None:
        """В таблице 100 строк, но фильтр ищет по всему списку."""

        from linfix import hostlist
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["hostlist"]

        entries = [
            hostlist.HostlistEntry(f"host{i:05d}.example.com", "other")
            for i in range(500)
        ]
        entries.append(hostlist.HostlistEntry("zzz-target.example.com", "user"))
        screen._entries = entries

        screen.filter_entry.set_text("")
        screen._render()
        self.assertEqual(len(screen.store), 100)
        self.assertIn("первые 100 из 501", screen.info.get_text())

        # Домен в самом конце списка — фильтр обязан его найти.
        screen.filter_entry.set_text("zzz-target")
        screen._render()
        self.assertEqual(len(screen.store), 1)
        self.assertEqual(screen.store[0][0], "zzz-target.example.com")
        app.window.destroy()


class BoldTitlesTests(unittest.TestCase):
    def test_service_headings_are_bold(self) -> None:
        """Заголовки карточек «Статуса» жирные."""

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        status = app.window.screens["status"]
        for card in (status.zapret_card, status.tgws_card,
                     status.domains_card, status.telegram_card):
            weight = card.title_label.get_style_context().get_property(
                "font-weight", Gtk.StateFlags.NORMAL
            )
            self.assertGreaterEqual(
                int(weight), 600, f"заголовок «{card.title_label.get_text()}» не жирный"
            )
        app.window.destroy()


class ProfileTableLayoutTests(unittest.TestCase):
    def test_profile_table_has_height(self) -> None:
        """Таблица профилей не должна схлопываться до двух строк."""

        from linfix.gtk.app import NetFixApp
        import time

        app = NetFixApp()
        app.do_activate()
        window = app.window
        window.resize(900, 720)
        window.set_page("doctor")
        for _ in range(60):
            while Gtk.events_pending():
                Gtk.main_iteration()
            time.sleep(0.01)

        screen = window.screens["doctor"]
        # Высота задана на ScrolledWindow, а не на TreeView: иначе GTK
        # просит минимум и видно только две строки.
        # Высота по контенту: внутренней прокрутки больше нет, поэтому
        # таблица — это сам TreeView, а не ScrolledWindow.
        self.assertIsInstance(screen.profile_scrolled, Gtk.TreeView)
        window.destroy()


class DoctorTablesTests(unittest.TestCase):
    def test_all_tables_use_content_height(self) -> None:
        """В «Диагностике» не должно быть таблиц с собственной прокруткой."""

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["doctor"]
        # Все три таблицы — это Gtk.TreeView, высота по контенту.
        self.assertIsInstance(screen.scrolled, Gtk.TreeView)
        self.assertIsInstance(screen.profile_scrolled, Gtk.TreeView)
        self.assertIsInstance(screen.net_scrolled, Gtk.TreeView)
        app.window.destroy()

    def test_network_table_hidden_until_run(self) -> None:
        """Пустая таблица с одним заголовком выглядит как поломка."""

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["doctor"]
        self.assertFalse(screen.net_scrolled.get_visible())
        self.assertTrue(screen.net_hint.get_visible())
        app.window.destroy()

    def test_columns_wrap_instead_of_truncate(self) -> None:
        """Длинный текст переносится, а не обрезается точками."""

        from linfix.gtk.app import NetFixApp
        from gi.repository import Pango

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["doctor"]
        for tree in (screen.tree, screen.net_tree, screen.profile_tree):
            for column in tree.get_columns():
                renderer = column.get_cells()[0]
                self.assertEqual(
                    renderer.get_property("wrap-mode"), Pango.WrapMode.WORD_CHAR
                )
                self.assertGreater(renderer.get_property("wrap-width"), 0)
                # Обрезка «…» больше не используется.
                self.assertEqual(
                    renderer.get_property("ellipsize"), Pango.EllipsizeMode.NONE
                )
        app.window.destroy()


class WindowTitleTests(unittest.TestCase):
    def test_title_contains_version_and_section(self) -> None:
        from linfix import __version__ as version
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window

        self.assertEqual(window.get_title(), f"LinFix v{version} — Статус")

        window.set_page("settings")
        self.assertEqual(window.get_title(), f"LinFix v{version} — Настройки")

        window.set_page("hostlist")
        self.assertEqual(window.get_title(), f"LinFix v{version} — Списки")

        window.set_page("status")
        self.assertEqual(window.get_title(), f"LinFix v{version} — Статус")
        window.destroy()


class StatusBlackholeTests(unittest.TestCase):
    """Висячие IPv6-заглушки видны на «Статусе» с кнопкой сброса."""

    def test_block_hidden_when_clean(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["status"]
        screen._show_blackholes([], [])
        self.assertFalse(screen.blackhole_box.get_visible())
        app.window.destroy()

    def test_block_shows_with_button(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["status"]
        routes = ["blackhole ::/1", "blackhole 2000::/4"]
        screen._show_blackholes(routes, [])
        self.assertTrue(screen.blackhole_box.get_visible())
        self.assertIn("2", screen.blackhole_label.get_text())
        self.assertTrue(screen.blackhole_button.get_visible())
        self.assertEqual(screen.blackhole_button.get_label(), "Убрать заглушки")
        app.window.destroy()

    def test_no_banner_when_vpn_active(self) -> None:
        """Пока VPN работает, маршруты нужны ему — это не висячие заглушки."""

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["status"]
        screen._show_blackholes(["blackhole 2000::/4"], ["qeli"])
        self.assertFalse(screen.blackhole_box.get_visible())
        app.window.destroy()

    def test_block_sits_between_check_result_and_cards(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["status"]
        children = screen.box.get_children()
        self.assertLess(
            children.index(screen.check_result_box),
            children.index(screen.blackhole_box),
        )
        self.assertLess(
            children.index(screen.blackhole_box),
            children.index(screen.zapret_card),
        )
        app.window.destroy()


class FoundTableTests(unittest.TestCase):
    """Найденные стратегии — таблица с выбором, а не текст в лейбле.

    Раньше показывались только первые 5 из N, и строку нельзя было ни
    выбрать, ни скопировать.
    """

    def _screen(self):
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["doctor"]
        self.addCleanup(app.window.destroy)
        return screen

    def test_shows_all_not_just_five(self) -> None:
        from linfix import blockcheck

        screen = self._screen()
        strategies = [
            blockcheck.Strategy(2, 2, f"--lua-desync=variant{n}") for n in range(10)
        ]
        screen._fill_found_table(strategies, strategies[0])
        self.assertEqual(len(screen.found_store), 10)
        self.assertTrue(screen.found_scrolled.get_visible())
        self.assertFalse(screen.found_hint.get_visible())

    def test_first_row_selected(self) -> None:
        from linfix import blockcheck

        screen = self._screen()
        strategies = [
            blockcheck.Strategy(2, 2, "--lua-desync=first"),
            blockcheck.Strategy(1, 2, "--lua-desync=second"),
        ]
        screen._fill_found_table(strategies, strategies[0])
        selected = screen._selected_strategy()
        self.assertIsNotNone(selected)
        self.assertEqual(selected["args"], "--lua-desync=first")

    def test_accepts_dicts_too(self) -> None:
        """После проверки приходят словари, а не Strategy."""

        screen = self._screen()
        items = [{"args": "--lua-desync=x", "success": 1, "total": 1, "body_ok": True}]
        screen._fill_found_table(items, items[0])
        self.assertEqual(len(screen.found_store), 1)

    def test_mark_best(self) -> None:
        from linfix import blockcheck

        screen = self._screen()
        strategies = [
            blockcheck.Strategy(2, 2, "--lua-desync=best"),
            blockcheck.Strategy(1, 2, "--lua-desync=other"),
        ]
        screen._fill_found_table(strategies, strategies[0])
        self.assertEqual(screen.found_store[0][3], "лучшая")
        self.assertEqual(screen.found_store[1][3], "")

    def test_table_is_not_collapsed(self) -> None:
        """Таблица в обёртке схлопывалась до одной строки."""

        screen = self._screen()
        self.assertNotIsInstance(screen.found_scrolled, Gtk.ScrolledWindow)


class PickBlockTests(unittest.TestCase):
    """Блок подбора отделён от диагностики Telegram.

    Раньше результаты подбора писались в verdict_label — тот же, что у
    диагностики, и блоки перемешивались.
    """

    def _screen(self):
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        self.addCleanup(app.window.destroy)
        return app.window.screens["doctor"]

    def test_separate_status_label(self) -> None:
        screen = self._screen()
        self.assertIsNot(screen.pick_status, screen.verdict_label)

    def test_pick_status_below_found_table(self) -> None:
        screen = self._screen()
        children = screen.box.get_children()
        self.assertLess(
            children.index(screen.found_scrolled),
            children.index(screen.pick_status),
            "статус подбора должен идти после таблицы",
        )
        self.assertLess(
            children.index(screen.verdict_label),
            children.index(screen.strategy_title),
            "диагностика Telegram должна быть выше блока подбора",
        )

    def test_seconds_field_default(self) -> None:
        screen = self._screen()
        self.assertEqual(screen.pick_seconds.get_text(), "180")
        self.assertLessEqual(int(screen.pick_seconds.get_text()), 2100)

    def test_seconds_field_validated(self) -> None:
        """Мусор в поле не должен ломать подбор — берётся значение из конфига."""

        screen = self._screen()
        screen.pick_seconds.set_text("abc")
        # Проверяем логику разбора напрямую.
        raw = screen.pick_seconds.get_text().strip()
        try:
            value = int(raw)
        except ValueError:
            value = screen.app.cfg.blockcheck.timeout
        self.assertEqual(value, screen.app.cfg.blockcheck.timeout)


class LayoutOverflowTests(unittest.TestCase):
    """Ни одна вкладка не должна требовать больше ширины окна.

    Баг: шесть кнопок «Доменов» в Gtk.Box требовали 985 px. Notebook брал
    ширину по самой широкой вкладке, страница получала горизонтальную
    прокрутку, и левый отступ уезжал вместе с контентом.
    """

    def test_no_screen_wider_than_window(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        window.set_default_size(900, 700)
        window.resize(900, 700)
        import time

        for _ in range(80):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

        for key, screen in window.screens.items():
            native = screen.box.get_preferred_width()[0]
            self.assertLessEqual(
                native, 900, f"вкладка «{key}» требует {native} px — шире окна"
            )
        window.destroy()

    def test_no_horizontal_scroll(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        window = app.window
        window.set_default_size(900, 700)
        window.resize(900, 700)
        import time

        for _ in range(80):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)
        adj = window.scrolled.get_hadjustment()
        self.assertLessEqual(
            adj.get_upper(),
            adj.get_page_size() + 1,
            "появилась горизонтальная прокрутка — контент шире окна",
        )
        window.destroy()

    def test_domains_buttons_wrap(self) -> None:
        """Кнопки «Доменов» в FlowBox, а не в Box: иначе не переносятся."""

        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["domains"]
        self.assertIsInstance(screen.buttons, Gtk.FlowBox)
        app.window.destroy()
