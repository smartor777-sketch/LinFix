# Ярлыки сервисов (LinFix)

Имитируем стиль Zapret2 (`zapret2-on.desktop` / `zapret2-off.desktop`), но для LinFix.

Созданы (в `~/.local/share/applications/`, имитируя формат `zapret2-on/off`):

- `linfix-zapret2-on.desktop` / `linfix-zapret2-off.desktop`
  - `Exec=pkexec systemctl start/stop zapret2`
- `linfix-tgwsproxy-on.desktop` / `linfix-tgwsproxy-off.desktop`
  - `Exec=systemctl --user start/stop tgwsproxy`
- `linfix-guard-on.desktop` / `linfix-guard-off.desktop`
  - `Exec=pkexec /usr/libexec/linfix-helper config.guard --enabled on/off --config /etc/linfix/config.toml`

Иконка:

- репозиторий: `data/icons/linfix.svg`
- установлена в `~/.local/share/icons/hicolor/scalable/apps/linfix.svg`
- все ярлыки имеют `Icon=linfix`, `Terminal=false`.

Особенности реализации:

- Имитируем поведение `zapret2`: для системных служб — `pkexec systemctl start/stop`,
  для user-служб TgWsProxy — `systemctl --user start/stop`.
- Guard меняет состояние через helper (`/usr/libexec/linfix-helper`), а не через
  ручной редактор, чтобы сохранялся бэкап и валидация.
- Все ярлыки обновляются при обновлении проекта; установка вручную через
  `data/icons/linfix.svg` + `data/icons` → `~/.local/share/icons/hicolor/scalable/apps/linfix.svg`.

## Почему так, а не через GUI?

В MVP без GTK есть только CLI (`linfix status`, `linfix doctor`, и т.д.),
поэтому «ярлышки в меню» реализованы как `.desktop`-записи (как для Zapret2),
которые выполняют команды с нужными состояниями сервисов.

## Название проекта

- Репозиторий/проект переименован в **LinFix** (пакет `linfix`, команда `linfix`).
- **SPEC.md оставлен без изменений** и описывает продукт **NetFix** (основное название
  продукта из SPEC, условное название), Linux-реализация — LinFix.
