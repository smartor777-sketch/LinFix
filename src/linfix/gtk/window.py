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

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_vexpand(True)
        self.scrolled.add(self.notebook)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.pack_start(self.scrolled, True, True, 0)
        main_box.pack_start(self.statusbar, False, False, 0)
        self.add(main_box)
        # Поведение: минимизируется (type hint NORMAL даёт кнопку minimize),
        # закрытие скрывает в трей, а не убивает приложение (см. delete-event).
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.set_deletable(True)
        self.set_resizable(True)
        self.set_keep_above(True)
        self.set_focus_on_map(True)
        self.connect("delete-event", self._on_delete_event)
        self.connect("window-state-event", self._on_window_state_event)
        self.show_all()

    def _on_delete_event(self, _widget, _event) -> bool:
        # Закрытие (X) прячет окно, трей остаётся живым.
        self.hide()
        return True

    def _on_window_state_event(self, _widget, event) -> bool:
        # Минимизация — скрываем окно в трей.
        state = event.get_state()
        if isinstance(state, tuple):
            state_mask = state[0] if state else 0
        else:
            state_mask = state
        if state_mask & Gdk.WindowState.ICONIFIED:
            self.hide()
            return True
        return False

    def set_page(self, key: str) -> None:
        for index, (name, _label) in enumerate(PAGES):
            if name == key:
                self.notebook.set_current_page(index)
                break
