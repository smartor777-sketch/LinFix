# LinFix Linux — ТЗ и план реализации

> **Для AI-агента на Linux-машине.** Этот документ самодостаточен: в нём есть контекст,
> архитектура, контракты команд, алгоритмы, подводные камни и критерии приёмки.
> Прочитай его целиком перед тем, как писать код. Если чего-то не хватает — сначала
> исследуй фактическую систему (раздел 18), не додумывай.

- **Версия документа:** 1.0
- **Дата:** 19.11.2026
- **Целевая система:** Linux Mint (Ubuntu-based), десктоп, systemd, nftables
- **Язык:** Python 3.10+, CLI; вторая фаза — GTK3 + PyGObject
- **Статус:** ТЗ на MVP, код ещё не начат

---

## 0. TL;DR для агента

Нужно сделать `linfix` — CLI-утилиту, которая превращает связку **zapret + TgWsProxy**
в управляемый продукт:

1. Находит уже установленный zapret (`nfqws` + nftables), проверяет, что он жив.
2. Ставит и настраивает TgWsProxy (Linux-бинарник с GitHub), поднимает user-службу systemd.
3. Держит в hostlist'е zapret актуальные CF-домены TgWsProxy — без них прокси не работает.
4. Диагностирует: `linfix doctor` показывает, что сломано и как чинить.
5. Защищает от «шторма соединений», если CF-путь умер.
6. Позже: профили под сети и GTK3-панель.

**Не переписывай zapret и не изобретай nftables-правила.** MVP интегрируется с уже
установленным zapret. Автоматическая установка zapret — вторая очередь.

Первые три действия на Linux-машине:

```bash
# 1. Изучить, что уже стоит
systemctl status zapret --no-pager
ps -eo pid,args | grep -E 'nfqws|tpws' | grep -v grep
nft list ruleset | head -50
cat /opt/zapret/config 2>/dev/null | head -50

# 2. Зафиксировать факты в docs/INVESTIGATION.md
# 3. Только после этого — M0 из раздела 18
```

---

## 1. Задача

Сделать аналог LinFix для Linux Mint: пользователь ставит пакет, запускает
`linfix setup`, отвечает на пару вопросов — и у него работает Telegram через
локальный прокси, а zapret обходит SNI-блокировку. После перезагрузки всё
поднимается само. Если что-то отвалилось — `linfix doctor` объясняет причину
человеческим языком.

**Что важно:** LinFix — это не один движок, а UX-обвязка. На Windows он управляет
WinDivert/zapret и TgWsProxy, на Android — VpnService. На Linux движки другие:
`nfqws` + NFQUEUE/nftables и Linux-сборка TgWsProxy. Поэтому это не порт, а
реализация той же идеи на других кирпичах.

---

## 2. Контекст: как это работает на Windows (проверено)

Схема, которая работает на машине автора:

```
Telegram Desktop
   │ MTProto, 127.0.0.1:1080
   ▼
TgWsProxy (локальный прокси, трей + воркер)
   │ TLS/WS на kws<DC>.<CF-домен>.co.uk:443
   ▼
[ zapret: драйвер WinDivert + winws ]
   │ hostfakesplit по SNI из hostlist
   ▼
Cloudflare
   ▼
Telegram DC
```

Ключевые факты, добытые в отладке (ноябрь 2026):

- **Zapret — не прокси.** Он портит/фрагментирует пакеты для доменов из списка,
  чтобы DPI провайдера не увидел SNI. Приложения о нём не знают.
- **Без zapret TgWsProxy не работает**: его CF-домены (`kws2.pclead.co.uk` и др.)
  блокируются по SNI. TCP-соединение устанавливается, но ответа нет → таймаут.
- **Без TgWsProxy zapret не чинит Telegram**: прямые IP дата-центров
  (`149.154.167.x`) заблокированы на уровне IP, desync их не спасает.
- **Все 20 CF-доменов должны быть в hostlist'е zapret.** После добавления
  домены стали отвечать за 0.3 с вместо 8-секундного таймаута.
- **Шторм соединений — реальная опасность.** При мёртвом CF-пути TgWsProxy
  накопил 2 531 established-сокет и ~30 ошибок в секунду; NAT роутера
  насытился, и перестали открываться все сайты. После остановки прокси сеть
  восстановилась.
- **Обновление zapret затирает списки.** LinFix переустановил zapret и удалил
  `C:\Zapret\lists\*-user.txt`; служба упала с ошибкой 1067. Вывод для Linux:
  наш слой должен хранить свой источник правды и уметь повторно применять
  домены после обновления.
- **Прямые WS к DC всегда будут падать** (`WS pool refill failed for DC2/DC4`) —
  это норма, рабочий путь идёт через CF. Не считать это фатальной ошибкой.
- **Секрет прокси уникален** для установки; ссылку `tg://proxy?...` нельзя
  публиковать, каждый берёт свою.

Эти факты — не теория, они воспроизведены на живой машине. Используй их как
требования к поведению.

---

## 3. Целевая платформа и ограничения

| Параметр | Решение |
|---|---|
| Дистрибутив | Linux Mint 21.3/22 (Ubuntu 22.04/24.04 base) |
| init | systemd (системный + пользовательский) |
| Firewall | nftables; iptables — только если система сама его использует |
| Права | root нужен для nftables/NFQUEUE и установки в `/opt`; TgWsProxy — user-служба |
| Secure Boot | не мешает: модуль ядра не собирается, NFQUEUE встроен |
| VPN/TUN | если default route уходит в tun (WireGuard/Hiddify), предупредить: zapret может не видеть трафик |
| IPv6 | проверить, что правила покрывают inet; при проблемах — тест с `curl -4` |

**Явно вне scope MVP:** OpenWrt/роутеры, Android, macOS, автоматический подбор
стратегии desync (это фаза 3), системный VPN, обход блокировок для всех
приложений (zapret и так действует системно, но прокси нужен только Telegram).

---

## 4. Функциональная карта LinFix → Linux

| Возможность LinFix | Linux MVP | Как |
|---|---|---|
| Установка zapret | интегрироваться с установленным; автоустановка — M2 | detect `/opt/zapret`, `zapret.service` |
| Запуск/остановка | да | `systemctl start/stop/restart zapret` |
| Списки доменов | да | merge в hostlist, который реально использует `nfqws` |
| CF-домены TgWsProxy | да, ключевая функция | fetch + decode + merge + test |
| Установка TgWsProxy | да | GitHub release, бинарник в `/opt/tgwsproxy` |
| Автозапуск | да | `systemctl enable zapret`; `systemctl --user enable tgwsproxy` |
| Подключение Telegram | да | печать `tg://`-ссылки из конфига |
| Диагностика | да | `linfix doctor` |
| Обновления | да, базовая | `linfix update` с проверкой версии и откатом |
| Профили под сети | фаза 2 | NetworkManager dispatcher |
| Автостратегия desync | фаза 3 | тест-харнесс, перебор пресетов |
| GUI | фаза 2 | GTK3 + PyGObject |
| Мобильное приложение | нет | вне scope |

---

## 5. Архитектура

### 5.1. Компоненты

```
┌──────────────────────────────┐
│ linfix (CLI, пользователь)   │
│  status / doctor / domains   │
│  tgws / logs / profiles      │
└──────────────┬───────────────┘
               │  привилегированные действия
               │  pkexec / sudo
               ▼
┌──────────────────────────────┐
│ linfix-helper (root, one-shot)│
│  whitelisted actions only     │
│  install / edit / restart     │
└──────────────┬───────────────┘
               │
     ┌─────────┴──────────┐
     ▼                    ▼
┌─────────────┐     ┌──────────────┐
│ zapret      │     │ TgWsProxy    │
│ system unit │     │ user unit    │
│ nfqws+nft   │     │ 127.0.0.1:1080│
└─────────────┘     └──────────────┘
```

**Решение: без постоянно живущего демона в MVP.** Привилегированные операции —
одноразовый `linfix-helper`, запускаемый через `pkexec` (в CLI можно и через
`sudo`). Это проще, безопаснее и достаточно для GUI: панель вызывает те же
действия через polkit. Если позже понадобится live-статус — добавим D-Bus,
но не начинай с демона.

### 5.2. Разделение прав

| Действие | Кто выполняет |
|---|---|
| чтение статуса, логов, конфигов | пользователь |
| `systemctl --user ... tgwsproxy` | пользователь |
| правка hostlist, `/opt`, nftables, `systemctl ... zapret` | `linfix-helper` (root) |
| NetworkManager dispatcher | root, вызывает helper напрямую |

`linfix-helper` принимает **только** фиксированный набор действий и
валидированные аргументы. Никакого `shell=True`, никаких путей от пользователя
без проверки, что они находятся под `/opt/linfix`, `/opt/zapret`,
`/opt/tgwsproxy`.

### 5.3. Структура репозитория

```
linfix/
├── SPEC.md                     # этот файл
├── README.md
├── pyproject.toml
├── src/linfix/
│   ├── __init__.py
│   ├── cli.py                  # argparse/typer, только пользовательские команды
│   ├── api.py                  # высокоуровневые операции, общие для CLI и GUI
│   ├── helper.py               # root-действия, entry point linfix-helper
│   ├── config.py               # чтение/валидация TOML
│   ├── zapret.py               # detect, hostlist, restart
│   ├── tgws.py                 # установка, конфиг, user unit, ссылка
│   ├── cfdomains.py            # fetch, decode, merge, test
│   ├── doctor.py               # проверки и отчёт
│   ├── profiles.py             # NetworkManager-профили (фаза 2)
│   ├── guard.py                # защита от шторма
│   └── util.py                 # run(), atomic_write(), backup(), logging
├── data/
│   ├── strategies/             # пресеты NFQWS_OPT (фаза 3)
│   ├── systemd/user/tgwsproxy.service
│   ├── networkmanager/90-linfix
│   └── polkit/org.linfix.policy
├── packaging/debian/
├── tests/
│   ├── test_cfdomains.py
│   ├── test_merge.py
│   ├── test_config.py
│   └── integration/run.sh
└── docs/INVESTIGATION.md       # факты о конкретной машине
```

---

## 6. Контракт CLI

Команда называется `linfix`. Коды возврата: `0` — успех, `1` — ошибка,
`2` — неверные аргументы, `3` — компонент не установлен, `4` — нет прав,
`5` — проверка не пройдена.

```
linfix status
    Краткая сводка: zapret active/enabled, nfqws pid, tgws active,
    порт 1080, число CF-доменов в hostlist, последнее обновление.

linfix setup [--yes] [--no-zapret-install]
    Мастер первой настройки. Проверяет систему, ставит TgWsProxy,
    добавляет CF-домены, включает автозапуск, запускает doctor.
    --yes — без вопросов (для скриптов).

linfix doctor [--json]
    Полная диагностика. Человеческий отчёт или JSON для GUI.
    Ненулевой код, если есть FAIL.

linfix domains list
    Показать CF-домены: источник, версия, какие есть в hostlist.

linfix domains update [--force]
    Скачать актуальный список с GitHub, декодировать, идемпотентно
    дописать отсутствующие, перезапустить zapret при изменениях.

linfix domains test [--all] [--json]
    Проверить SNI-доступность доменов (TLS-хендшейк).
    По умолчанию — выборка 3–5 доменов, --all — все 20.

linfix tgws install [--version X]
    Скачать Linux-бинарник, проверить SHA256, поставить в /opt/tgwsproxy.

linfix tgws start|stop|restart|status
    Управление user-службой systemd.

linfix tgws link [--qr]
    Напечатать tg://-ссылку с секретом из конфига.
    --qr — QR в терминале (опционально, зависимость qrcode).

linfix tgws config [--port 1080] [--verbose on|off] ...
    Безопасно менять ключи конфига TgWsProxy (бэкап + валидация).

linfix logs [--follow] [--lines 50]
    journalctl для zapret и tgwsproxy, плюс хвост proxy.log.

linfix update [--check]
    Проверить и обновить TgWsProxy и CF-домены.
    --check — только показать доступные версии.

linfix profile list|set|clear   # фаза 2
linfix uninstall [--keep-config]
```

Все команды с `--json` (где указано) должны возвращать стабильную схему —
на неё будет опираться GTK-панель.

---

## 7. Конфигурация

Системный конфиг: `/etc/linfix/config.toml` (root, 0644, секретов нет).

```toml
[general]
log_level = "info"          # debug | info | warn | error
language  = "ru"            # ru | en

[zapret]
install_dir   = "/opt/zapret"
service       = "zapret"
hostlist      = ""          # пусто = определить автоматически (см. 8.2)
restart_on_change = true

[tgws]
install_dir = "/opt/tgwsproxy"
binary      = "/opt/tgwsproxy/TgWsProxy"
port        = 1080
host        = "127.0.0.1"
autostart   = true

[lists]
cf_domains_url = "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt"
update_days    = 7
marker         = "# linfix: tgws cf domains"

[guard]
enabled          = true
window_seconds   = 60
error_threshold  = 20       # столько "CF proxy failed" за окно — тревога

[profiles]
enabled = false             # фаза 2
```

Пользовательский конфиг TgWsProxy — тот, который создаёт само приложение.
Путь на Linux **надо определить фактически** (см. 9.1): вероятные варианты
`~/.config/TgWsProxy/config.json` или `~/.local/share/TgWsProxy/config.json`.
Не хардкодь, пока не проверил.

Состояние: `/var/lib/linfix/state.json` — установленные версии, дата последнего
обновления доменов, последний профиль. Пишет только helper.

---

## 8. Интеграция с zapret (Linux)

### 8.1. Что такое zapret на Linux

`nfqws` из проекта `bol-van/zapret`: перехватывает пакеты через NFQUEUE,
применяет desync (в т.ч. `hostfakesplit`) к доменам из hostlist. Требует root
и nftables/iptables-правила, которые создаёт официальный установщик
`install_easy.sh`. Обычно создаётся служба `zapret.service`, конфиг лежит в
`/opt/zapret/config`, списки — в `/opt/zapret/ipset/`.

**Проверь на целевой машине, а не принимай на веру:** пути, имя юнита и
формат конфига могут отличаться между версиями.

### 8.2. Как определить реальный hostlist

Порядок (остановись на первом успешном):

1. `systemctl cat zapret` и `ps -eo args | grep nfqws` — найти аргументы
   `--hostlist=...` и `--hostlist-exclude=...`. Это истина.
2. Разобрать `/opt/zapret/config`: переменные вида `HOSTLIST`, `HOSTLIST_USER`
   (если есть).
3. Fallback: `/opt/zapret/ipset/zapret-hosts-user.txt`.

Писать домены нужно **в тот файл, который реально указан в аргументах
`nfqws`**. Если файла нет — создать с правами 0644, владелец root.

### 8.3. Формат hostlist

- Одна строка — один домен, без схемы и порта.
- `#` в начале строки — комментарий (проверено: zapret это переваривает).
- Базовая запись `pclead.co.uk` покрывает `kws2.pclead.co.uk` (проверено на
  Windows-версии; на Linux проверь тестом из 10.3).
- Пустые строки игнорируются.
- Кодировка ASCII/UTF-8 без BOM, переводы строк LF (CRLF тоже переваривается,
  но пиши LF).

### 8.4. Изменение списка

1. Прочитать файл, собрать set существующих доменов в нижнем регистре.
2. Вычислить отсутствующие.
3. Сделать бэкап: `<file>.bak-linfix-YYYYMMDD-HHMMSS` (один раз за операцию).
4. Атомарно записать: temp в той же директории → `os.replace`.
5. Сохранить владельца/права (root:root, 0644).
6. Если что-то изменилось и `restart_on_change` — `systemctl restart zapret`.
7. Проверить, что служба active и `nfqws` жив; если нет — откатить бэкап
   и вернуть ошибку с текстом из `journalctl -u zapret -n 50`.

### 8.5. Установка zapret

MVP: **не устанавливать автоматически**. Если zapret не найден — напечатать
понятную инструкцию:

```
Zapret не найден. Установите его официальным установщиком:
  git clone https://github.com/bol-van/zapret
  cd zapret && sudo ./install_easy.sh
Выберите nfqws + systemd. После установки повторите: linfix setup
```

Автоустановка (M2) должна использовать официальный установщик или его
неинтерактивный режим, если он есть. Если режима нет — не изобретай свои
nftables-правила, а задокументируй ручной шаг. Лучше меньше автоматизации,
чем сломанный firewall.

### 8.6. Стратегии

MVP: не менять `NFQWS_OPT`, использовать то, что поставил установщик.
Фаза 3: пресеты в `data/strategies/*.conf` — это полные строки `NFQWS_OPT`,
проверенные на Mint/Ubuntu. Применение: бэкап `/opt/zapret/config`, замена
только присваивания `NFQWS_OPT`, рестарт, проверка `doctor`. Автоподбор —
отдельный тест-харнесс, не в MVP.

---

## 9. Интеграция с TgWsProxy (Linux)

### 9.1. Что скачать

Релизы: `https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest`

Актуально на 19.11.2026 — v1.11.4, есть ассеты:

- `TgWsProxy_linux_amd64` (raw, ~57 МБ)
- `TgWsProxy_linux_amd64.deb`
- `TgWsProxy_linux_amd64.rpm`

MVP: raw-бинарник в `/opt/tgwsproxy/TgWsProxy`, `chmod 0755`. Не ставь `.deb`:
он притащит свои зависимости и пути, которые мы не контролируем.

Скачивание: HTTPS, версия пиннится, SHA256 сверяется с `digest` из GitHub API
(поле `digest` у asset'а). При несовпадении — удалить файл и прервать.

### 9.2. Первый запуск и конфиг

TgWsProxy — приложение с треем, но на Linux его можно держать как user-службу.
**Проверь `--help` и фактическое поведение:**

- поддерживает ли `--headless` / `--no-tray`;
- куда пишет конфиг и лог;
- можно ли указать `--config`;
- как ведёт себя без DISPLAY.

Если headless-режима нет — запускай в пользовательской сессии (user unit),
трей будет доступен. Для десктопа это нормально.

Конфиг (JSON) — те же ключи, что на Windows:

```json
{
  "host": "127.0.0.1",
  "port": 1080,
  "cfproxy": true,
  "cfproxy_user_domain": [],
  "cfproxy_worker_domain": [],
  "h2": true,
  "verbose": false,
  "pool_size": 4,
  "secret": "<сгенерировать один раз>"
}
```

Не перезаписывай конфиг целиком: читай существующий, меняй только нужные
ключи, сохраняй бэкап. Секрет — 32 hex-символа, генерируется при первом
запуске; не логируй его.

### 9.3. User-служба

`~/.config/systemd/user/tgwsproxy.service`:

```ini
[Unit]
Description=TgWsProxy (Telegram WS proxy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/tgwsproxy/TgWsProxy
Restart=on-failure
RestartSec=3
# если приложение пишет в stdout — journald подхватит автоматически

[Install]
WantedBy=default.target
```

Порядок с zapret: zapret — системная служба и стартует раньше пользовательской
сессии, так что `After=zapret.service` в user-юните не сработает (разные
менеджеры). Это нормально: если zapret ещё не поднялся, TgWsProxy переживёт
несколько неудачных попыток, а `Restart=on-failure` повторит. В doctor проверяй
оба юнита.

Автозапуск: `systemctl --user enable --now tgwsproxy`. Для старта без входа
в сессию не нужен (трей бессмысленен без сессии); не включай linger без
необходимости.

### 9.4. Ссылка для Telegram

Секрет из конфига, ссылка:

```
tg://proxy?server=127.0.0.1&port=1080&secret=dd<secret>
```

Префикс `dd` — часть формата (на Windows в логе было именно `dd...`).
Команда `linfix tgws link` печатает ссылку; в GUI — кнопка «Подключить
Telegram» и QR.

После рестарта прокси Telegram может остаться без соединения — в инструкции
для пользователя должно быть «нажмите ссылку ещё раз» или «Переподключить».

### 9.5. Лог и его расположение

Кандидаты:

- `~/.config/TgWsProxy/proxy.log`
- `~/.local/share/TgWsProxy/proxy.log`
- stdout в journald (если так настроено)

Определи фактически: `lsof -p <pid> | grep -i log`, `ls -la` вокруг конфига.
Если лог недоступен — guard (раздел 12) работает в режиме «только journald»
или отключается с предупреждением.

---

## 10. CF-домены TgWsProxy

### 10.1. Зачем

TgWsProxy ходит на `kws<DC>.<domain>.co.uk`. Провайдер режет эти домены по SNI.
Zapret маскирует SNI, но только для доменов, которые есть в его hostlist.
Без этого — таймауты, `CF proxy failed`, шторм переподключений.

### 10.2. Источник и декодирование

Источник: `https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt`

Файл содержит **закодированные** домены вида `virkgj.com`. Алгоритм из
исходников (`proxy/config.py`, функция `_dd`):

```python
def decode_cf_domain(s: str) -> str:
    """virkgj.com -> pclead.co.uk"""
    if not s.endswith(".com"):
        return s
    core = s[:-4]
    shift = sum(ch.isalpha() for ch in core)
    out = []
    for ch in core:
        if ch.isalpha():
            base = ord("a") if ch.islower() else ord("A")
            out.append(chr((ord(ch) - base - shift) % 26 + base))
        else:
            out.append(ch)
    return "".join(out) + ".co.uk"
```

Проверочные векторы для тестов:

| Вход | Выход |
|---|---|
| `virkgj.com` | `pclead.co.uk` |
| `vmmzovy.com` | `offshor.co.uk` |
| `mkuosckvso.com` | `cakeisalie.co.uk` |
| `zaewayzmplad.com` | `noskomnadzor.co.uk` |
| `khgrre.com` | `ebally.co.uk` |
| `nothingthere` (без `.com`) | `nothingthere` (без изменений) |

Валидация после декодирования: `^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$`.
Если больше половины строк не прошли — считать декодирование сломанным
(автор сменил алгоритм), использовать fallback и написать в отчёте.

### 10.3. Fallback-список (актуален на 19.11.2026)

```
pclead.co.uk
offshor.co.uk
cakeisalie.co.uk
noskomnadzor.co.uk
lovetrue.co.uk
sorokdva.co.uk
pyatdesyatdva.co.uk
kartoshka.co.uk
sorokodin.co.uk
pyatdesyatodin.co.uk
notelega.co.uk
ebally.co.uk
nebally.co.uk
havegreatday.co.uk
pomogite.co.uk
fixtelega.co.uk
sadnews.co.uk
onedaychamp.co.uk
stopblocking.co.uk
nothingthere.co.uk
```

Используй его, только если GitHub недоступен или декодирование сломалось.
В нормальном режиме — всегда свежий список с GitHub.

### 10.4. Merge

Идемпотентно, как в 8.4. Маркер `# linfix: tgws cf domains` + дата.
Повторный запуск не должен менять файл и не должен рестартовать zapret.

### 10.5. Проверка доменов (SNI)

Не полагайся на HTTP-код: цель — понять, проходит ли TLS-хендшейк с нужным SNI.

```python
import socket, ssl

def sni_ok(host: str, timeout: float = 5.0) -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return True, tls.version() or "TLS"
    except (socket.timeout, TimeoutError):
        return False, "timeout (SNI blocked or zapret not applied)"
    except ssl.SSLError as e:
        return False, f"tls error: {e.reason}"
    except OSError as e:
        return False, f"tcp error: {e}"
```

Тестируй `kws2.<domain>` и `kws4.<domain>` (это DC2 и DC4). Успех — хендшейк
прошёл, независимо от HTTP-статуса. Таймаут — плохо. TCP-ошибка — скорее всего
IP-блок или нет маршрута.

### 10.6. Обновление

- Раз в 7 дней (настраивается) при `linfix update` или `linfix domains update`.
- Если список изменился — merge + рестарт zapret + отчёт.
- Если домены пропали из источника — **не удаляй** старые автоматически:
  они могут быть ещё рабочими. Только добавь новые и покажи diff.
- Храни `state.json`: дата обновления, sha256 файла, количество доменов.

---

## 11. Диагностика (`linfix doctor`)

Отчёт — таблица проверок с PASS/WARN/FAIL и подсказкой. Пример:

```
Проверка                         Статус  Детали
-------------------------------  ------  ------------------------------------
Система                          PASS    Linux Mint 22, systemd, nft
Модуль nfnetlink_queue           PASS    загружен
Служба zapret                    PASS    active (enabled), pid 1234
Правила nftables                 PASS    queue 200, 2 правила
TgWsProxy: служба                PASS    active (user), pid 2345
TgWsProxy: порт 1080             PASS    LISTEN 127.0.0.1:1080
CF-домены в hostlist             PASS    20/20
CF-домены: TLS-тест              PASS    5/5 (0.3 s)
Прямые WS к DC                   WARN    падают — это норма, работаем через CF
Telegram подключён               WARN    нет соединений на 1080
Guard                            PASS    ошибок нет
```

Проверки:

1. **ОС и пакеты:** `nft`, `systemctl`, `curl`, Python; предупреждение, если
   не Mint/Ubuntu (всё равно может работать).
2. **NFQUEUE:** модуль `nfnetlink_queue` в `/proc/modules` или `modprobe -n`.
3. **zapret:** `systemctl is-active/enabled`, `nfqws` в `ps`, аргументы
   `--hostlist`.
4. **nftables:** в `nft list ruleset` есть queue/num, совпадающий с `nfqws`.
5. **TgWsProxy:** user-юнит active, процесс, `ss -ltnp` на 1080, что слушает
   именно 127.0.0.1, а не 0.0.0.0.
6. **Конфиг:** `cfproxy: true`, порт совпадает, секрет непустой.
7. **Домены:** все в hostlist, TLS-тест выборки.
8. **Логи:** за последние N минут нет лавины `CF proxy failed`; отдельно
   посчитать `WS pool unavailable`, `no fallback`.
9. **Маршрут:** default route не в tun/VPN; если tun — WARN с объяснением.
10. **Конфликты:** порт 1080 занят другим процессом; два экземпляра
    TgWsProxy (на Windows их два — трей и воркер — это норма; на Linux
    проверь, что не запущены две независимые копии).

`--json` — та же информация структурой:

```json
{
  "ok": false,
  "checks": [
    {"id": "zapret.service", "status": "pass", "details": "active (enabled), pid 1234"},
    {"id": "domains.tls", "status": "fail", "details": "1/5 timeout", "hint": "linfix domains update && sudo systemctl restart zapret"}
  ]
}
```

---

## 12. Защита от шторма соединений (guard)

Это требование из реального инцидента: при мёртвом CF-пути TgWsProxy создал
тысячи сокетов, и NAT роутера перестал справляться — «лёг» весь интернет.

Поведение:

- Раз в 30–60 с читать новые строки лога TgWsProxy (отслеживать offset,
  не парсить файл целиком).
- Считать `CF proxy failed` за окно `window_seconds`.
- Если количество ≥ `error_threshold` **и** TLS-тест доменов провален —
  это не случайность, а мёртвый путь:
  1. записать WARN в журнал;
  2. остановить `tgwsproxy` (или перевести в degraded, если есть режим);
  3. показать пользователю: «CF-путь недоступен, прокси остановлен, чтобы не
     перегрузить сеть. Запустите `linfix domains update` и проверьте
     `linfix domains test`».
- Не останавливать прокси, если ошибки есть, но TLS-тест проходит: возможны
  единичные сбои.
- В `linfix status` показывать состояние guard.

Опционально: `linfix guard on|off`. По умолчанию on.

---

## 13. Автозапуск и профили сетей

### 13.1. Автозапуск

- `zapret.service` — системная, `enable` делает установщик. `linfix setup`
  проверяет и включает, если выключено.
- `tgwsproxy.service` — user-юнит, `systemctl --user enable`.
- После перезагрузки: zapret стартует до логина, TgWsProxy — при входе
  пользователя. Telegram, запущенный при входе, может подключиться раньше
  прокси; он переподключится сам, но в инструкции стоит советовать подождать
  пару секунд или перезапустить Telegram.
- Проверка в doctor: `systemctl is-enabled zapret` и
  `systemctl --user is-enabled tgwsproxy`.

### 13.2. Профили (фаза 2)

- `nmcli -t -f UUID,NAME,TYPE connection show --active` — определить текущее
  соединение.
- `/etc/NetworkManager/dispatcher.d/90-linfix` (root, 0755) вызывает
  `/usr/libexec/linfix-helper profile.apply "$CONNECTION_UUID"`.
- Профили в `/etc/linfix/profiles.toml`:

```toml
[profiles."<uuid>"]
name = "Домашний Wi-Fi"
strategy = "general"
# позже: mode = "cf" | "direct" | "auto"
```

- Если профиль не задан — использовать `general`.
- Dispatcher работает в минимальном окружении: не полагайся на `PATH`,
  используй абсолютные пути, логируй в journald с тегом `linfix`.
- Смена профиля = смена стратегии/списка + рестарт zapret, только если
  реально что-то изменилось.

MVP: можно только логировать UUID и активный профиль, без смены стратегии.
Но структуру файлов заложи сразу.

---

## 14. GTK3-панель (фаза 2)

- Python 3 + PyGObject, **GTK3** (Mint 22 поставляет GTK3; GTK4 не тащить).
- Не дублировать логику: панель импортирует `linfix.api`, а привилегированные
  действия вызывает через `pkexec /usr/libexec/linfix-helper ...`.
- Экраны:
  1. **Статус** — карточки zapret / TgWsProxy / домены / Telegram, кнопки
     «Перезапустить», «Подключить Telegram».
  2. **Настройка** — мастер: проверить систему, поставить TgWsProxy,
     добавить домены, включить автозапуск.
  3. **Домены** — список, дата обновления, кнопка «Обновить и проверить»,
     таблица TLS-тестов.
  4. **Диагностика** — вывод doctor, кнопка «Скопировать отчёт».
  5. **Настройки** — порт, verbose, guard, автозапуск.
  6. **Логи** — journalctl + proxy.log, фильтры.
- Долгие операции — в `threading.Thread`, обновление UI через
  `GLib.idle_add`. Никаких блокирующих вызовов в главном потоке.
- Трей: `XApp.StatusIcon` (Cinnamon/Mint), fallback `AyatanaAppIndicator3`.
  Иконка отражает состояние: зелёная / жёлтая (warn) / красная (fail).
- Локализация: gettext, `ru` по умолчанию, `en` как fallback.
- Пакет `.deb`: `python3-gi`, `gir1.2-gtk-3.0`, `python3-xapp` (recommends),
  `nftables`, `curl`, `ca-certificates`, `network-manager` (recommends).
- Desktop file, иконка, polkit policy `org.linfix.policy` с action
  `org.linfix.helper` (auth_admin_keep).

---

## 15. Безопасность

1. **Скачивание:** только HTTPS, версия пиннится, SHA256 из GitHub API
   (`asset.digest`). Несовпадение — прервать, не запускать.
2. **Helper:** белый список действий; аргументы валидируются; пути
   канонизируются (`Path.resolve()`) и проверяются на префикс разрешённых
   директорий; никакого `shell=True`; таймауты на все вызовы.
3. **Секреты:** `tg://`-ссылка печатается только по явной команде; не писать
   секрет в journald; конфиг TgWsProxy — 0600.
4. **Сеть:** TgWsProxy слушает только `127.0.0.1`. Проверять в doctor, что
   нет `0.0.0.0:1080`.
5. **Бэкапы:** перед любой правкой `/opt/zapret/config` и hostlist — копия
   с меткой времени. При провале проверки — откат.
6. **Никаких «оптимизаций» firewall:** не добавлять свои nft-правила поверх
   zapret; только управлять тем, что создал установщик.
7. **Логи:** не логировать содержимое конфигов целиком; только ключи и статусы.

---

## 16. Тестирование и критерии приёмки

### 16.1. Unit-тесты (pytest)

- `decode_cf_domain` — таблица векторов из 10.2, плюс мусорные входы.
- merge hostlist — идемпотентность, сохранение комментариев, CRLF/LF, пустой файл.
- парсинг конфига — дефолты, неверные типы, отсутствующие секции.
- валидация аргументов helper — попытка path traversal, лишние аргументы.
- профили — матчинг UUID, отсутствующий профиль → general.
- guard — подсчёт ошибок по синтетическому логу, порог, отсутствие ложных
  срабатываний при проходящем TLS-тесте.

### 16.2. Интеграционные проверки (VM)

Ubuntu 24.04 / Mint 22, обычный пользователь + sudo:

1. Чистая система, zapret установлен вручную.
2. `linfix setup --yes` → код 0, `linfix doctor` без FAIL.
3. `linfix domains test --all` → все домены OK (при работающем zapret).
4. `linfix tgws link` → ссылка открывается в Telegram, медиа грузится.
5. Перезагрузка → оба юнита active, Telegram работает после переподключения.
6. `linfix domains update` повторно → «изменений нет», zapret не рестартует.
7. Убрать домены из hostlist → `doctor` FAIL с подсказкой; `domains update`
   возвращает всё назад.
8. Остановить zapret → `doctor` FAIL, `domains test` таймауты; запуск
   zapret лечит.
9. Guard: искусственно заполнить лог `CF proxy failed` + заблокировать
   домены → прокси останавливается, в статусе понятное сообщение.
10. `linfix uninstall --keep-config` → юниты удалены, `/opt/tgwsproxy` удалён,
    конфиг остался.

### 16.3. Критерии приёмки MVP

- Установка и настройка на свежей Mint 22 — не более 10 минут, без ручного
  редактирования файлов.
- После ребута всё поднимается само.
- `linfix doctor` объясняет любую поломку человеческим языком и даёт команду
  для исправления.
- Ни одного случая «шторма», при котором падает вся сеть: guard срабатывает.
- Повторные запуски идемпотентны: ничего не дублируется, лишних рестартов нет.

---

## 17. Подводные камни (учти заранее)

**Из Windows-опыта:**

- CF-домены режутся по SNI, а не по IP; TCP может соединяться, а данных нет.
  Проверяй именно TLS-хендшейк.
- Обновление zapret затирает списки. Держи CF-домены в нашем state и
  переприменяй после обновления/переустановки.
- Прямые WS к DC падают — это норма, не надо «чинить».
- Два процесса TgWsProxy (трей + воркер) — норма, не убивай «дубликат».
- Секрет уникален; не публикуй свою ссылку.
- При мёртвом CF — лавина соединений; нужен guard.

**Linux-специфика:**

- `nfqws` требует root и NFQUEUE; в контейнерах/песочницах (Flatpak, Snap)
  работать не будет. Ставь только на хост.
- nftables vs iptables: установщик zapret сам выбирает; не смешивай.
- NetworkManager dispatcher — root, минимальное окружение, без `PATH`.
- `pkexec` не сохраняет окружение и переменные пользователя; передавай всё
  аргументами, не через env.
- User-служба не видит системные `After=`; не пытайся выстроить порядок
  между системным и пользовательским менеджерами.
- VPN/TUN (WireGuard, Hiddify, OpenVPN) может перехватить default route —
  zapret увидит трафик, но прокси может не достучаться. Doctor должен
  предупреждать.
- IPv6: если провайдер режет по SNI и в v6, правила должны покрывать inet;
  при странностях тестируй `curl -4`.
- Порт 1080 часто занят (другой прокси, SSH-туннель). Проверяй `ss -ltnp`
  до запуска и предлагай свободный порт.
- Mint/Cinnamon: GTK3 и XAppStatusIcon; AppIndicator может требовать
  `gir1.2-ayatanaappindicator3-0.1`.

---

## 18. План работ (milestones)

### M0. Каркас — 0.5 дня

- Репозиторий, `pyproject.toml`, пакет `linfix`, логирование, `util.run()`.
- `linfix --help`, `linfix status` (заглушка), конфиг с дефолтами.
- `docs/INVESTIGATION.md` с фактами о целевой машине.
- Unit-тесты на декодер и merge (можно до реализации UI).

### M1. Zapret + домены — 1–2 дня

- detect zapret: служба, процесс, аргументы `nfqws`, hostlist.
- `domains list/update/test`, merge, бэкапы, рестарт, проверка.
- `status` показывает реальные данные.
- Тест на живой машине: добавить домены → TLS-тест зелёный.

### M2. TgWsProxy — 1 день

- скачивание, SHA256, установка в `/opt/tgwsproxy`.
- конфиг (detect path, аккуратные правки), user-юнит, enable/start.
- `tgws link`, `tgws start/stop/restart/status`.
- Проверка: Telegram подключается, медиа грузится.

### M3. Doctor + guard — 1 день

- все проверки из раздела 11, `--json`.
- guard из раздела 12.
- `setup` — мастер, который доводит систему до зелёного doctor.

### M4. Профили + автозапуск — 1 день

- проверка `enable` для обоих юнитов.
- dispatcher, `profiles.toml`, логирование текущей сети.
- (опционально) смена стратегии, если уже есть пресеты.

### M5. Пакет .deb — 1–2 дня

- `debian/`, зависимости, polkit policy, desktop file, иконка.
- `linfix uninstall`.
- Проверка установки на чистой VM.

### M6. GTK3-панель — 1–2 недели

- экраны из раздела 14, трей, потоки, polkit.
- локализация ru/en.
- интеграция с `linfix.api`, без дублирования логики.

**Итого MVP (M0–M5): ~1 неделя** при аккуратной работе. GTK — отдельно.

---

## 19. Открытые вопросы (решить с человеком)

1. Какая версия Mint: 21.3 или 22? (влияет на Python и GTK)
2. Zapret уже установлен на целевой машине или `linfix` должен его ставить?
   MVP рассчитан на «уже установлен».
3. Telegram используется через прокси вручную (кнопка) или нужно
   авто-подключение при старте? Авто-подключение — вне MVP.
4. Нужен ли английский интерфейс или только русский?
5. Есть ли VPN/TUN, который может конфликтовать? Проверить `ip route`.

Если ответов нет — использовать дефолты из этого документа и зафиксировать
их в `docs/DECISIONS.md`.

---

## 20. Приложение

### 20.1. Полезные ссылки

| Что | URL |
|---|---|
| TgWsProxy (релизы) | https://github.com/Flowseal/tg-ws-proxy/releases |
| Список CF-доменов (закодированный) | https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt |
| Исходники декодера | https://github.com/Flowseal/tg-ws-proxy/blob/main/proxy/config.py |
| Документация CfProxy | https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/RU/CfProxy.md |
| Zapret (Linux) | https://github.com/bol-van/zapret |

### 20.2. Глоссарий

- **SNI** — имя домена в TLS-хендшейке; по нему DPI решает, блокировать ли.
- **Desync / hostfakesplit** — приём zapret: отправить поддельный/разрезанный
  пакет, чтобы DPI не собрал картинку, а сервер принял соединение.
- **NFQUEUE** — механизм ядра Linux, отдающий пакеты в userspace (`nfqws`).
- **CF-путь** — маршрут TgWsProxy через Cloudflare к DC Telegram.
- **DC** — дата-центр Telegram (1–5, 203).
- **Guard** — наш сторож, останавливающий прокси при лавине ошибок.

### 20.3. Что считать успехом

Пользователь на Mint:

1. Ставит пакет.
2. Запускает `linfix setup`, отвечает «да».
3. Открывает ссылку из `linfix tgws link`.
4. Видит, что фото и видео в Telegram грузятся.
5. После ребута ничего не делает — всё работает.
6. Если сломалось — `linfix doctor` говорит, что именно, и что нажать.

Вот и весь продукт. Всё остальное — удобство вокруг этих шести пунктов.
