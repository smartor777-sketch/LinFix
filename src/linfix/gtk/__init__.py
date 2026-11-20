"""GTK3-панель LinFix (M6).

Панель управления: использует только `linfix.api` для данных; привилегированные
действия выполняются через helper (`pkexec /usr/libexec/linfix-helper`).
Долгие операции в `threading.Thread`, обновление UI — через `GLib.idle_add`.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
