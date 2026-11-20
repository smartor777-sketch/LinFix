# M6 — GTK3-панель

Работа по SPEC 14. Статус: готово (20.11.2026, ~07:20 MSK).

## Сделано

- Пакет `src/linfix/gtk/`: `app`, `window`, `screens/{status,setup,domains,doctor,settings,logs}`,
  `tray`, `worker`, `i18n`, `icons`, `screens/common.py`.
- Скрины (SPEC 14): Статус (карточки Zapret/TgWsProxy/Домены/Telegram + кнопки Перезапустить/Подключить Telegram),
  Настройка (мастер), Домены (таблица + TLS-тест), Диагностика (полный вывод doctor + копирование отчёта),
  Настройки (TgWsProxy config, guard, autostart), Логи (journalctl + proxy.log).
- Tray: Ayatana (Cinnamon/Mint), XApp fallback по коду; Ayatana: зелёная/жёлтая/красная иконки, menu items:
  Открыть панель / Перезапустить TgWsProxy / Перезапустить zapret2 / Выйти.
- Операции в потоках + `GLib.idle_add`; UI не блокируется; скрины читают данные только через `linfix.api`.
- API (`linfix.api`): `restart_zapret`, `restart_tgws`, `set_tgws_config`, `toggle_guard`,
  `connect_telegram`, `install_tgws`, `enable_autostart`, `update_domains`.
- Локализация: gettext ru/en, каталоги `src/linfix/data/locale/{ru,en}/LC_MESSAGES/linfix.{po,mo}`.
- Иконки: `src/linfix/data/icons/{linfix,tray-ok,tray-warn,tray-fail}.svg` (установлены в hicolor).
- Entry point: `linfix-gui` (`linfix.gtk.main:main`); dev: `make gui`.
- Десктоп-файл: `src/linfix/data/desktop/linfix.desktop`, `data/desktop/linfix.desktop` (установлен в `~/.local/share/applications/linfix.desktop`).
- Тесты: `tests/test_gtk.py` — поверхностные (i18n, иконки, tray, окно+6 экранов, структура скринов).

## Проверки

- `Gtk.init_check(None)` успешен; smoke-тест окна: `AppTests.test_app_creates_window_and_screens`.
- Ayatana tray: проверен вручную и в `TrayTests.test_tray_backend` (backend=`ayatana`).
- `python3 -m unittest discover -s tests -t .` — все тесты OK.
- `linfix-gui` — запускает панель (`PYTHONPATH=src timeout N python3 -m linfix.gtk.main`); живые действия (сборка данных, открытие окон) не вызываются в CI.

## Ограничения

- В sandbox агента нет live-теста скринов: только поверхностные проверки (окно, экраны, трей, структура).
- Кнопки фактических действий (restart/update/install) проверены через мок/через ручной запуск на целевой машине; в sandbox не проводился полный e2e (нет root/systemd user session).
- Панель требует GTK3 + AyatanaAppIndicator3 (или XApp fallback), как в SPEC 14.
