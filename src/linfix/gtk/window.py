"""Главное окно LinFix: объединяет экраны в Gtk.Notebook (SPEC 14)."""

from __future__ import annotations

from gi.repository import GLib, Gdk, Gtk

from .i18n import _
from .screens import DoctorScreen, DomainsScreen, LogsScreen, SettingsScreen, SetupScreen, StatusScreen

PAGES: list[tuple[str, str]] = [
    ("status", _("Статус")),
    ("setup", _("Мастер настройки")),
    ("domains", _("Домены")),
    ("doctor", _("Диагностика")),
    ("settings", _("Настройки")),
    ("logs", _("Логи")),
]

# Активная вкладка должна быть визуально очевидна, иначе легко спутать
# соседние разделы («Мастер настройки» и «Настройки»).
_TAB_CSS = b"""
.tab-button.active {
    background-image: none;
    background-color: #3584e4;
    color: #ffffff;
    font-weight: bold;
}
"""


def _install_tab_css() -> None:
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_TAB_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


class NetFixWindow(Gtk.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title=_("LinFix — статус"), default_width=800, default_height=560)
        self.app = app
        self._restoring = False
        _install_tab_css()
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

        self.notebook.set_show_tabs(False)
        self.notebook.set_scrollable(False)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_vexpand(True)
        self.scrolled.add(self.notebook)

        # Кнопки разделов — закреплены в шапке (не скроллятся с контентом).
        self.tab_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_top=4,
            margin_bottom=4,
            margin_left=4,
            margin_right=4,
        )
        self._tab_buttons: dict[str, Gtk.Button] = {}
        for index, (key, label) in enumerate(PAGES):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("tab-button")
            btn.connect("clicked", lambda _b, i=index: self.notebook.set_current_page(i))
            self._tab_buttons[key] = btn
            self.tab_bar.pack_start(btn, False, False, 0)
        self.notebook.connect("switch-page", lambda _nb, _p, i: self._activate_tab(i))
        self._activate_tab(0)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.pack_start(self.tab_bar, False, False, 0)
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
        if state is None:
            # GTK3: у EventWindowState get_state() — None; флаги в new_window_state.
            state = getattr(event, "new_window_state", None)
        if isinstance(state, tuple):
            state_mask = state[0] if state else 0
        else:
            state_mask = state
        if state_mask is None:
            return False
        if state_mask & Gdk.WindowState.ICONIFIED:
            # Не скрываем, если сейчас восстанавливаем окно из трея.
            if self._restoring:
                return True
            self.hide()
            return True
        return False

    def _end_restoring(self) -> bool:
        self._restoring = False
        return False

    def restore_from_tray(self) -> None:
        """Восстановить окно из трея: принудительно показать + снять ICONIFIED.

        Гоночные ICONIFIED события, приходящие после восстановления (~2 сек),
        игнорируются — иначе окно перестаёт разворачиваться повторно.
        """

        self._restoring = True
        GLib.timeout_add(2000, self._end_restoring)
        self.present()
        self.set_visible(True)
        self.deiconify()
        self.show_all()

    def set_page(self, key: str) -> None:
        for index, (name, _label) in enumerate(PAGES):
            if name == key:
                self.notebook.set_current_page(index)
                break

    def _activate_tab(self, index: int) -> None:
        for btn in self._tab_buttons.values():
            btn.get_style_context().remove_class("active")
        if 0 <= index < len(PAGES):
            key = PAGES[index][0]
            btn = self._tab_buttons.get(key)
            if btn is not None:
                btn.get_style_context().add_class("active")
                # Фокус следует за активной вкладкой, чтобы рамка фокуса
                # не подсвечивала соседний раздел.
                btn.grab_focus()
            screen = self.screens.get(key)
            hook = getattr(screen, "on_shown", None)
            if callable(hook):
                hook()
