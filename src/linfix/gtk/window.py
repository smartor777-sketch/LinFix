"""Главное окно LinFix: объединяет экраны в Gtk.Notebook (SPEC 14)."""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from .i18n import _
from .screens import DoctorScreen, DomainsScreen, LogsScreen, SettingsScreen, SetupScreen, StatusScreen

PAGES: list[tuple[str, str]] = [
    ("status", _("Статус")),
    ("setup", _("Настройка")),
    ("domains", _("Домены")),
    ("doctor", _("Диагностика")),
    ("settings", _("Настройки")),
    ("logs", _("Логи")),
]


class NetFixWindow(Gtk.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title=_("LinFix — статус"), default_width=800, default_height=560)
        self.app = app
        self.notebook = Gtk.Notebook()
        self.screens: dict[str, object] = {}

        screen_classes = [StatusScreen, SetupScreen, DomainsScreen, DoctorScreen, SettingsScreen, LogsScreen]
        for (key, label), screen_class in zip(PAGES, screen_classes):
            screen = screen_class(app)
            self.screens[key] = screen
            tab = Gtk.Label(label=label)
            self.notebook.append_page(screen.box, tab)

        self.statusbar = Gtk.Statusbar()
        self.statusbar.set_hexpand(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.pack_start(self.notebook, True, True, 0)
        main_box.pack_start(self.statusbar, False, False, 0)
        self.add(main_box)
        # Поведение «на верху»: просим WM поднять окно при активации.
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_keep_above(True)
        self.set_focus_on_map(True)
        self.show_all()

    def set_page(self, key: str) -> None:
        for index, (name, _label) in enumerate(PAGES):
            if name == key:
                self.notebook.set_current_page(index)
                break
