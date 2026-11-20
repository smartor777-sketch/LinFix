"""Трей: AyatanaAppIndicator3 (аналог Zapret2), fallback — XApp.StatusIcon (Cinnamon/Mint)."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gtk


class Tray:
    """Системный трей; отражает состояние через цвет иконки.

    Бэкенды (в порядке предпочтения):
    1. `AyatanaAppIndicator3` (доступен в Ubuntu/Mint 22);
    2. `XApp.StatusIcon` (Cinnamon/Mint), если доступен;
    3. fallback — трей не создаётся (окно доступно через панель).
    """

    def __init__(self, app, icon_path: str, tooltip: str = "") -> None:
        self.app = app
        self.icon_path = icon_path
        self.tooltip = tooltip
        self.backend: str | None = None
        self.indicator = None
        self._build()

    def _build(self) -> None:
        try:
            import gi

            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator

            self.backend = "ayatana"
            self.indicator = AppIndicator.Indicator.new(
                "linfix", self.icon_path or "linfix", AppIndicator.IndicatorCategory.APPLICATION_STATUS
            )
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.indicator.set_menu(self._make_menu())
            if self.tooltip and hasattr(self.indicator, "set_tooltip_text"):
                self.indicator.set_tooltip_text(self.tooltip)
        except Exception:
            try:
                import gi

                gi.require_version("XApp", "2.0")
                from gi.repository import XApp

                self.backend = "xapp"
                self.indicator = XApp.StatusIcon(self.icon_path or "linfix")
                self.indicator.set_tooltip_text(self.tooltip or "LinFix")
                self.indicator.connect("clicked", lambda *_: self.app.show())
            except Exception:
                self.backend = None
                self.indicator = None

    def set_icon(self, icon_path: str | None) -> None:
        self.icon_path = icon_path or "linfix"
        if self.indicator is None:
            return
        if self.backend == "ayatana":
            self.indicator.set_icon(self.icon_path)
        elif self.backend == "xapp":
            self.indicator.set_icon_name(self.icon_path)  # type: ignore[attr-defined]

    def set_tooltip(self, text: str) -> None:
        if self.indicator is not None and hasattr(self.indicator, "set_tooltip_text"):
            self.indicator.set_tooltip_text(text)

    def _indicator_menu_item(self, label_text: str, callback: Callable) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label_text)
        item.connect("activate", lambda *_: callback())
        return item

    def _make_menu(self) -> Gtk.Menu:
        from .i18n import _

        menu = Gtk.Menu()

        item = Gtk.MenuItem(label=_("Открыть панель"))
        item.connect("activate", lambda *_: self.app.show())
        menu.append(item)

        item = Gtk.MenuItem(label=_("Перезапустить TgWsProxy"))
        item.connect("activate", lambda *_: self.app.restart_tgws_tray())
        menu.append(item)

        item = Gtk.MenuItem(label=_("Перезапустить zapret2"))
        item.connect("activate", lambda *_: self.app.restart_zapret_tray())
        menu.append(item)

        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)

        item = self._indicator_menu_item(_("Выйти"), self.app.quit)
        menu.append(item)

        menu.show_all()
        return menu

    def destroy(self) -> None:
        if self.indicator is not None:
            try:
                self.indicator.set_status(3)  # AyatanaAppIndicator3: ACTIVE=1..DEACTIVATED
            except Exception:
                pass
            try:
                self.indicator.run_dispose()
            except Exception:
                pass
