# LinFix

Управление обходом блокировок на Linux из одной панели: **zapret2** (NFQUEUE/DPI)
+ **TgWsProxy** (Telegram WS proxy). Ставится одним `.deb`, настраивается
мастером «Далее → Далее», дальше живёт в трее.

![LinFix](docs/screenshot-settings.png)

## Что умеет

- **Мастер настройки**: проверка системы → установка zapret2 из GitHub →
  установка TgWsProxy из GitHub → CF-домены → автозапуск. Пароль спрашивается
  только на привилегированных шагах (polkit/pkexec).
- **Статус**: служба, процесс, nfqws2, hostlist, домены, TgWsProxy, Telegram.
- **Настройки**: порт/хост TgWsProxy, verbose-логи и их размер, guard,
  автозапуск, размер лога, очистка логов, ссылка `tg://proxy` + «Копировать».
- **Домены**: обновление списка CF-доменов с GitHub, merge в hostlist,
  рестарт с проверкой и откатом при падении.
- **Диагностика**: отчёт по системе (nft, NFQUEUE, службы, домены, TLS, VPN, guard).
- **Логи**: journalctl (zapret2 + tgwsproxy) + хвост `proxy.log`, поиск.
- **Трей**: цвет иконки = состояние, меню управления службами.

## Установка

### Из .deb (обычный путь)

```bash
sudo apt install ./linfix_0.1.0_all.deb
linfix-gui
```

Зависимости подтянутся через apt: `python3-gi`, `gir1.2-gtk-3.0`, `nftables`,
`curl`, `ca-certificates`, `git`, `pkexec`, `systemd`.

После установки: **LinFix** в меню → вкладка **«Мастер настройки»** → «Далее».
Мастер поставит zapret2 и TgWsProxy из GitHub, применит CF-домены и включит
автозапуск. Требуется только ввод пароля в диалоге polkit.

### Из исходников (разработка)

```bash
git clone https://github.com/<owner>/LinFix.git
cd LinFix
make test     # 197 тестов
make gui      # запуск панели без установки
make deb      # → dist/linfix_0.1.0_all.deb
```

## CLI

```bash
linfix status                 # состояние zapret и TgWsProxy
linfix doctor                 # диагностика (+ --json)
linfix domains update         # CF-домены → hostlist → рестарт
linfix tgws install           # TgWsProxy из GitHub (SHA256)
linfix tgws link              # tg://proxy-ссылка
linfix logs                   # журналы служб
linfix setup                  # то же, что мастер, но в терминале
```

Привилегированные операции идут через `linfix-helper` (белый список действий,
pkexec + polkit policy). В dev-режиме — через `pkexec`/`sudo` из рабочей копии.

## Логи и их размер

- `~/.config/TgWsProxy/proxy.log` — ротация **5 МБ + 1 бэкап** (≈10 МБ),
  настраивается в «Настройки → Размер лога, МБ» или `linfix tgws config --log-max-mb 10`.
- `journalctl` — лимит системы (`SystemMaxUse`, по умолчанию 500 МБ).
- «Настройки → Очистить логи» удаляет `proxy.log` и бэкап.

## Безопасность

- Скачивание только по HTTPS, бинарник TgWsProxy — с проверкой SHA256.
- Список действий helper'а фиксирован, пути канонизируются и проверяются.
- Конфиг TgWsProxy пишется с правами `0600`, изменения — с бэкапом.
- В hostlist пишется помеченный блок, есть откат при неудачном рестарте.

## Структура

```
src/linfix/            api.py, cli.py, helper.py, tgws.py, zapret.py, ...
src/linfix/gtk/        GTK3-панель (окно, экраны, трей)
packaging/debian/      control, launchers, postinst/prerm, сборка .deb
data/                  polkit policy, systemd user-юниты, иконки
tests/                 197 unit-тестов (stdlib unittest)
```

## Лицензия

MIT
