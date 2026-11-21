# Параметры подключения к Telegram (снимок 21.11.2026)

Документ фиксирует параметры, которые **фактически используются сейчас** на этом ПК для подключения к Telegram. Это не рекомендованные настройки, а снимок рабочей конфигурации.

## Схема

```
Telegram-клиент (MTProto)
   -> 127.0.0.1:1080  (TgWsProxy / mtproto WS bridge, секрет dd…)
   -> Cloudflare H2 / CF proxy (домены из пула)
   -> DC Telegram (явно переопределены DC2/DC4)
```

Дополнительно включён zapret (`C:\Zapret\service.bat`) — он подменяет/маскирует трафик, но само подключение к Telegram идёт через локальный TgWsProxy.

## Источники данных

| Источник | Что дал |
|---|---|
| `C:\Program Files\NetFix\settings.json` | пути к zapret и TgWsProxy, общие флаги |
| `C:\Users\Alex\AppData\Roaming\NetFix\settings.json` | актуальные флаги включения, активные моды |
| `C:\Users\Alex\AppData\Roaming\TgWsProxy\config.json` | активная конфигурация прокси |
| `C:\Users\Alex\AppData\Roaming\TgWsProxy\proxy.log` | запуск 21.11.2026 16:40:44, строка `Connect:`, факты роутинга |
| `C:\Users\Alex\AppData\Roaming\NetFix\zapret_configs.json` | сохранённые конфигурации zapret |

Процессы на момент снимка: `NetFix.exe`, `winws.exe`, 2 × `TgWsProxy.exe` (запущены 21.11.2026 16:40–16:41).

## TgWsProxy — активные параметры

Версия: **TG WS Proxy 1.11.4** (`C:\Zapret\TgWsProxy.exe`, `tgwsproxy_version.txt = v1.11.4`).

| Параметр | Значение |
|---|---|
| `host` | `127.0.0.1` |
| `port` | `1080` |
| `secret` | `019b15e32644ccd7be33170d9eef1f4b` |
| `dc_ip` | `2:149.154.167.220`, `4:149.154.167.220` |
| `cfproxy` | `true` (авто) |
| `cfproxy_user_domain` | `[]` |
| `cfproxy_worker_domain` | `[]` |
| `cfproxy_user_domain_enabled` | `false` |
| `cfproxy_worker_enabled` | `false` |
| `h2` | `true` (CF H2 media включён) |
| `ws_keepalive_interval` | `30` |
| `buf_kb` | `256` |
| `pool_size` | `4` |
| `verbose` | `false` |
| `check_updates` | `true` |
| `log_max_mb` | `5` |
| `autostart` | `false` |
| `force_test_dc` | `false` |
| `no_secure` | `false` |
| `language` | `ru` |
| `appearance` | `auto` |

### Готовая ссылка для Telegram-клиента

```
tg://proxy?server=127.0.0.1&port=1080&secret=dd019b15e32644ccd7be33170d9eef1f4b
```

Ссылка локальная: работает только пока на этом ПК запущен TgWsProxy и слушает `127.0.0.1:1080`.

### Cloudflare-домены

Свои домены в конфиге не заданы. На старте лог сообщает:

```
CF proxy domain pool updated from GitHub (20 domains)
```

Источник пула: `https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt`.
Локальная копия доменов (на 19.11.2026) лежит в [`cf-domains.txt`](../cf-domains.txt) этого репозитория. В логе маршрутизация media идёт через домены `*.nothingthere.co.uk`, `*.sorokdva.co.uk` и др. из этого пула (имена частично маскируются в логе).

### Фактический роутинг по логу (21.11.2026)

- `DC1…DC5` -> `trying CF proxy`, WS-пул для этих DC часто недоступен (`WS pool unavailable -> fallback`).
- Media (`DC2 media`, `DC4 media`) -> `ROUTE ... H2 lane=1/2 channel=N host=<cf-домен>`.
- Счётчик на 23:15: `total=730 active=4 ws=0 h2=610 h2_tcp=44 h2_req=3888 tcp_fb=0 cf=706 up=3.5MB down=91.7MB`.
  Т.е. в данный момент работают именно H2/CF-каналы, а не прямой WS.

## Интеграция с NetFix

Из `C:\Users\Alex\AppData\Roaming\NetFix\settings.json`:

| Параметр | Значение |
|---|---|
| `zapretPath` | `C:\Zapret\service.bat` |
| `tgWsProxyPath` | `C:\Zapret\TgWsProxy.exe` |
| `enableZapret` | `true` |
| `enableTgWsProxy` | `true` |
| `autostartZapret` | `false` |
| `autostartTgWsProxy` | `false` |
| `autostartApp` | `false` |
| `autoUpdates` | `true` |
| `tgWsProxyCheckUpdates` | `true` |
| `forceNetworkOk` | `true` |
| `activeListMods` | `ДЛЯ ВСЕГО!!!!!` |
| `activeHostsMods` | `!ДЛЯ ВСЕГО 2!`, `Обход ChatGbt, Gemin` |
| `activeStrategyMods` | `[]` |

В `C:\Program Files\NetFix\settings.json` (установочная копия) те же пути, но `enableZapret`/`enableTgWsProxy` отсутствуют — рабочими считаются значения из `AppData\Roaming`.

Версия zapret: **1.10.4**. В `C:\Zapret\lists\list-general.txt` — 1484 байта (на 19.11.2026), пользовательские списки `list-general-user.txt`, `list-exclude-user.txt`, `ipset-exclude-user.txt` пусты.

## Важно

- `secret` в `tg://`-ссылке — это credential. Файл лежит в приватном репозитории, но при публикации/шаринге его нужно отозвать и сгенерировать новый (`openssl rand -hex 16` -> префикс `dd`).
- Значения `dc_ip`, `secret` и пул доменов могут меняться через UI TgWsProxy/NetFix — при изменении обновить этот снимок.
