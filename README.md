# LinFix

**Графическая панель для Linux**, которая превращает связку
**zapret2** (обход DPI через NFQUEUE) + **TgWsProxy** (MTProto-прокси для Telegram)
в управляемый продукт: поставил `.deb`, открыл окно, нажал «Далее» — работает.

![Статус](docs/screenshot-status.png)

## Как это выглядит

Панель — это одно окно с закреплёнными разделами: **Статус**, **Мастер настройки**,
**Домены**, **Диагностика**, **Настройки**, **Логи**. Плюс иконка в трее, которая
меняет цвет по состоянию системы.

| Мастер настройки | Настройки |
|---|---|
| ![Мастер](docs/screenshot-wizard.png) | ![Настройки](docs/screenshot-settings.png) |

| Диагностика |
|---|
| ![Диагностика](docs/screenshot-doctor.png) |

## Что умеет

- **Мастер настройки** — пять шагов «Далее»: проверка системы → установка
  zapret2 из GitHub → установка TgWsProxy из GitHub → CF-домены → автозапуск.
  Пароль спрашивается только на привилегированных шагах (polkit/pkexec).
- **Статус** — служба, процесс nfqws2, очередь/разметка, hostlist, домены,
  TgWsProxy, порт и telegram-ссылка в одном экране.
- **Домены** — обновление списка CF-доменов с GitHub, точечная проверка TLS,
  добавление в hostlist с бэкапом и откатом при неудачном рестарте.
- **Диагностика** — отчёт по системе: NFQUEUE, nftables, службы, hostlist,
  TLS до CF-доменов, VPN/TUN, guard.
- **Настройки** — порт, хост, verbose, размер лога, режим TgWsProxy
  (трей/CLI), guard, автозапуск, управление службами, ссылка `tg://proxy`.
- **Логи** — journalctl обеих служб + хвост `proxy.log`, с поиском.
- **Трей** — цвет иконки отражает состояние, меню управляет службами.

## Guard: защита от шторма соединений

Если CF-путь Telegram отваливается, клиент в цикле переподключается, и
TgWsProxy забивает канал соединениями — в итоге тормозит весь трафик, а не
только Telegram. **Guard** останавливает прокси в этом сценарии.

Раз в минуту он читает только новые строки `proxy.log`, считает
`CF proxy failed` за окно (по умолчанию 20 за 60 с) и, если порог превышен,
проверяет TLS до CF-доменов. Прокси останавливается **только если ошибки и
TLS-тест провален** — одного всплеска ошибок недостаточно.

```bash
linfix guard status   # включён ли и когда срабатывал
linfix guard check    # проверить сейчас
linfix guard on|off   # переключить
```

В панели — **Настройки → Guard**. Подробности: [docs/GUARD.md](docs/GUARD.md).

## Установка

### Из .deb

```bash
sudo apt install ./linfix_0.1.5_all.deb
```

Затем запустите **LinFix** из меню (или `linfix-gui`), откройте
**«Мастер настройки»** и нажимайте «Далее».

Зависимости ставятся через apt: `python3-gi`, `gir1.2-gtk-3.0`, `nftables`,
`curl`, `ca-certificates`, `git`, `pkexec`, `systemd`.

### Из исходников

```bash
git clone https://github.com/smartor777-sketch/LinFix.git
cd LinFix
make test     # 204 теста
make gui      # запуск панели без установки
make deb      # → dist/linfix_<version>_all.deb
```

## Два режима TgWsProxy

TgWsProxy существует в двух видах, и LinFix поддерживает оба:

| | `tray` | `cli` |
|---|---|---|
| X11 / `DISPLAY` | нужен | **не нужен** |
| Иконка в трее | есть | нет |
| Для чего | десктоп | сервер, SSH, headless |

Переключение — в **«Настройки → Режим TgWsProxy»** или из терминала:

```bash
linfix tgws config --mode cli
linfix tgws restart
```

Порт, secret, `tg://`-ссылка и ротация лога общие для обоих режимов — они
хранятся в `~/.config/TgWsProxy/config.json`. Если `DISPLAY` недоступен,
LinFix сам выбирает `cli`.

## CLI (второй интерфейс)

Вся функциональность панели доступна и из терминала — удобно для серверов
и скриптов:

```bash
linfix status                  # сводка по компонентам
linfix doctor                  # полная диагностика (+ --json)
linfix domains update          # CF-домены → hostlist → рестарт
linfix tgws install            # TgWsProxy из GitHub (SHA256)
linfix tgws config --mode cli  # headless-режим
linfix tgws link               # tg://proxy-ссылка
linfix logs                    # журналы служб
linfix setup                   # мастер настройки в терминале
```

Привилегированные операции идут через `linfix-helper` — отдельный бинарник
с белым списком действий, вызываемый через pkexec и polkit-policy.

## Логи и их размер

- `~/.config/TgWsProxy/proxy.log` — ротация **5 МБ + 1 бэкап** (≈10 МБ).
  Меняется в «Настройки → Размер лога, МБ» или
  `linfix tgws config --log-max-mb 10`.
- «Настройки → Очистить логи» удаляет `proxy.log` и его бэкап.
- `journalctl` ограничен системно (`SystemMaxUse`, по умолчанию 500 МБ).

## Безопасность

- Скачивание только по HTTPS; бинарник TgWsProxy — с проверкой SHA256.
- Список действий helper'а фиксирован, пути канонизируются и проверяются.
- Конфиг TgWsProxy пишется с правами `0600`, изменения — с бэкапом.
- В hostlist пишется помеченный блок; при неудачном рестарте — откат.

## Структура

```
src/linfix/            api.py, cli.py, helper.py, tgws.py, zapret.py, …
src/linfix/gtk/        GTK3-панель: окно, экраны, трей
packaging/debian/      control, обёртки, postinst/prerm, сборка .deb
data/                  polkit policy, systemd user-юниты, иконки
tests/                 204 unit-теста (stdlib unittest)
```

## Лицензия

MIT
