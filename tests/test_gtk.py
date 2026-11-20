from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from linfix.gtk import icons  # noqa: E402
from linfix.gtk.i18n import install, _  # noqa: E402
from linfix.gtk.tray import Tray  # noqa: E402

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
        self.assertEqual(app.window.notebook.get_n_pages(), 6)
        self.assertEqual(
            sorted(app.window.screens),
            ["doctor", "domains", "logs", "settings", "setup", "status"],
        )
        status = app.window.screens["status"]
        self.assertEqual(status.zapret_card.title_label.get_text(), "Zapret")
        domains = app.window.screens["domains"]
        self.assertEqual(domains.store.get_n_columns(), 3)
        doctor = app.window.screens["doctor"]
        self.assertEqual(doctor.store.get_n_columns(), 4)
        settings = app.window.screens["settings"]
        self.assertTrue(settings.port_entry.get_text())
        self.assertTrue(hasattr(settings, "zapret2_switch"))
        self.assertTrue(settings.zapret2_switch.get_label() if hasattr(settings.zapret2_switch, "get_label") else True)
        setup = app.window.screens["setup"]
        self.assertTrue(setup.step_label.get_text())
        logs = app.window.screens["logs"]
        self.assertTrue(logs.full_text is not None)
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
        self.assertEqual(set(screen.telegram_card.rows), {"connections", "status"})
        self.assertTrue(screen.buttons.get_children())
        self.assertTrue(screen.box.get_visible())
        app.window.destroy()


if __name__ == "__main__":
    unittest.main()
