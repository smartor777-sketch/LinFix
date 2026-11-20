"""Сборочные функции и базовые виджеты для экранов LinFix (M6)."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gtk

from ..i18n import _


def label(text: str, wrap: bool = False, css_class: str | None = None) -> Gtk.Label:
    result = Gtk.Label(label=text)
    result.set_xalign(0.0)
    result.set_yalign(0.5)
    if wrap:
        result.set_line_wrap(True)
        result.set_max_width_chars(60)
    if css_class:
        result.get_style_context().add_class(css_class)
    return result


def pack(box: Gtk.Box, child: Gtk.Widget) -> None:
    box.pack_start(child, False, False, 0)


def pack_expand(box: Gtk.Box, child: Gtk.Widget) -> None:
    box.pack_start(child, True, True, 0)


def button(text: str, handler: Callable[[object], None], css_class: str | None = None) -> Gtk.Button:
    result = Gtk.Button(label=text)
    if css_class:
        result.get_style_context().add_class(css_class)
    result.connect("clicked", handler)
    return result


def entry(text: str = "") -> Gtk.Entry:
    result = Gtk.Entry()
    result.set_text(text)
    return result


def switch(active: bool = False) -> Gtk.Switch:
    result = Gtk.Switch()
    result.set_active(active)
    return result


class Card(Gtk.Box):
    """Карточка: заголовок + строки «ключ — значение»."""

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_hexpand(True)
        self.get_style_context().add_class("card")
        self.title_label = label(title, css_class="card-title")
        self.pack_start(self.title_label, False, False, 0)
        self.rows: dict[str, Gtk.Label] = {}

    def add(self, key: str, text: str) -> Gtk.Label:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_hexpand(True)
        key_label = label(text)
        value = label("—")
        value.set_hexpand(True)
        row.pack_start(key_label, False, False, 0)
        row.pack_start(value, True, True, 0)
        self.pack_start(row, False, False, 0)
        self.rows[key] = value
        return value

    def set(self, key: str, text: str) -> None:
        self.rows[key].set_text(text or "—")


def status_label(status: str) -> Gtk.Label:
    colors = {"pass": "green", "warn": "yellow", "fail": "red"}
    color = colors.get(status, "gray")
    result = label(status.upper())
    result.get_style_context().add_class("status-label")
    result.get_style_context().add_class(color)
    return result


def scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    result = Gtk.ScrolledWindow()
    result.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    result.add(child)
    return result


def table_from_store(
    store: Gtk.ListStore,
    columns: list[str],
    tree: Gtk.TreeView | None = None,
) -> Gtk.TreeView:
    tree = tree or Gtk.TreeView(model=store)
    for index, column_title in enumerate(columns):
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(column_title, renderer, text=index)
        column.set_expand(True)
        tree.append_column(column)
    return tree


def toast(app, text: str) -> None:
    if app.window is not None:
        app.window.statusbar.push(
            app.window.statusbar.get_context_id("linfix"),
            text,
        )


def message(app, text: str) -> None:
    if app.window is None:
        return
    dialog = Gtk.MessageDialog(
        transient_for=app.window,
        flags=Gtk.DialogFlags.MODAL,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=text,
    )
    dialog.run()
    dialog.destroy()


def screen_box(title: str, *children: Gtk.Widget, spacing: int = 12, margin: int = 12) -> Gtk.Box:
    result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    result.set_margin_top(margin)
    result.set_margin_bottom(margin)
    result.set_margin_start(margin)
    result.set_margin_end(margin)
    if title:
        result.pack_start(label(title, css_class="screen-title"), False, False, 0)
    for child in children:
        result.pack_start(child, False, False, 0)
    return result
