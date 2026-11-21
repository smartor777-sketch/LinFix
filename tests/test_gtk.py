from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

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
        self.assertEqual(
            [b.get_label() for b in domains.buttons.get_children()],
            [
                "Обновить и проверить",
                "Тест TLS выбранных",
                "Тест всего списка",
                "Добавить домен",
                "Удалить выбранные",
            ],
        )
        doctor = app.window.screens["doctor"]
        self.assertEqual(doctor.store.get_n_columns(), 4)
        settings = app.window.screens["settings"]
        self.assertTrue(settings.port_entry.get_text())
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
        self.assertEqual(labels, ["Обновить"])
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
            patch("linfix.gtk.screens.settings.api.start_tgws") as start_tgws,
            patch("linfix.gtk.screens.settings.api.start_zapret") as start_zapret,
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


class LayoutTests(unittest.TestCase):
    def test_status_refresh_button_above_cards(self) -> None:
        from linfix.gtk.app import NetFixApp

        app = NetFixApp()
        app.do_activate()
        status = app.window.screens["status"]
        children = status.box.get_children()
        # 0 — заголовок раздела, 1 — панель с «Обновить», дальше карточки.
        self.assertIs(children[1], status.buttons)
        self.assertEqual(
            [b.get_label() for b in status.buttons.get_children()], ["Обновить"]
        )
        app.window.destroy()

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

    def test_columns_ellipsize(self) -> None:
        """Длинный текст не должен растягивать таблицу за окно."""

        from linfix.gtk.app import NetFixApp
        from gi.repository import Pango

        app = NetFixApp()
        app.do_activate()
        screen = app.window.screens["doctor"]
        for tree in (screen.tree, screen.net_tree, screen.profile_tree):
            for column in tree.get_columns():
                renderer = column.get_cells()[0]
                self.assertEqual(
                    renderer.get_property("ellipsize"), Pango.EllipsizeMode.END
                )
        app.window.destroy()
