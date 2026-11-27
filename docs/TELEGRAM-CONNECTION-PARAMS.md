# Параметры подключения к Telegram (снимок 21.11.2026, обновлено 22.11.2026)

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
| `%APPDATA%\NetFix\settings.json` | актуальные флаги включения, активные моды |
| `%APPDATA%\TgWsProxy\config.json` | активная конфигурация прокси |
| `%APPDATA%\TgWsProxy\proxy.log` | запуск 21.11.2026 16:40:44, строка `Connect:`, факты роутинга |
| `%APPDATA%\NetFix\zapret_configs.json` | сохранённые конфигурации zapret |

Процессы на момент снимка: `NetFix.exe`, `winws.exe`, 2 × `TgWsProxy.exe` (запущены 21.11.2026 16:40–16:41).

## TgWsProxy — активные параметры

Версия: **TG WS Proxy 1.11.4** (`C:\Zapret\TgWsProxy.exe`, `tgwsproxy_version.txt = v1.11.4`).

| Параметр | Значение |
|---|---|
| `host` | `127.0.0.1` |
| `port` | `1080` |
| `secret` | `<REDACTED — сгенерировать заново>` |
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
tg://proxy?server=127.0.0.1&port=1080&secret=dd<REDACTED>
```

Ссылка локальная: работает только пока на этом ПК запущен TgWsProxy и слушает `127.0.0.1:1080`.

### Cloudflare-домены

Свои домены в конфиге не заданы. Пул обновляется раз в час, в логе это выглядит так:

```
2026-11-22 04:44:47  INFO  tg-mtproto-proxy  CF proxy domain pool updated from GitHub (20 domains)
```

Источник пула: `https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt`.
Локальная копия списка не нужна: `scripts/decode-cf-domains.py --upstream`
скачивает файл и раскодирует его. В файле лежат `.com`-коды
(`virkgj.com` → `pclead.co.uk`), а не сами домены.

### Текущий CF-сервер (22.11.2026 04:47 MSK)

Живые исходящие TLS-соединения процессов `TgWsProxy.exe` (PID 14860, 16044), все на порту `443`:

| Удалённый адрес | Число соединений |
|---|---:|
| `172.67.214.67:443` | 3 |
| `104.21.33.146:443` | 1 |
| `104.21.70.196:443` | 1 |
| `104.21.78.5:443` | 1 |
| `172.67.138.236:443` | 1 |

Все адреса принадлежат Cloudflare (диапазоны `104.21.x.x`, `172.67.x.x`).

Последние строки `ROUTE` в логе (`04:45:28–04:45:33`):

| DC | Домен (SNI/Host) | Канал |
|---|---|---|
| `DC2 media` | `kw*.nothingthere.co.uk` | H2, lane 1, channels 909–921 |
| `DC4 media` | `kw*.notelega.co.uk` | H2, lane 25, channels 916–920 |
| `DC1 media` | `kw*.noskomnadzor.co.uk` | H2, lane 5, channel 915 |

Ранее для `DC4 media` также использовались `kw*.sorokdva.co.uk` / `kw*.sorokodin.co.uk`.

В логе имена маскируются (`kw**.nothin******.c*.uk`, `kw**.note****.c*.uk`); полные имена восстановлены по шаблону маски и раскодированному списку доменов. У этих `*.co.uk`-доменов нет A-записей в DNS (только SOA `ingrid.ns.cloudflare.com`), то есть прокси подключается напрямую к IP Cloudflare, а домен использует как SNI/Host.

### Фактический роутинг по логу

- `DC1…DC5` -> `trying CF proxy`, WS-пул для этих DC почти всегда недоступен (`WS pool unavailable -> fallback`).
- Media (`DC1/DC2/DC4 media`) -> `ROUTE ... H2 lane=N channel=N host=<cf-домен>`.
- 21.11.2026 23:15: `total=730 active=4 ws=0 h2=610 h2_tcp=44 h2_req=3888 tcp_fb=0 cf=706 up=3.5MB down=91.7MB`.
- 22.11.2026 04:46: `total=1288 active=4 ws=0 h2=921 h2_tcp=73 h2_req=11348 h2_err=24 tcp_fb=0 cf=1264 masked=0 err=928 pool=0/1211 cf_pool=n/a up=8.6MB down=425.2MB`.

Прямой WS не работает (`ws=0`), весь трафик идёт через CF/H2-каналы.

## Интеграция с NetFix

Из `%APPDATA%\NetFix\settings.json`:

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

- `secret` в `tg://`-ссылке — это credential. В публичной версии он заменён на `<REDACTED>`; если он уже использовался, отзовите его и сгенерируйте новый (`openssl rand -hex 16` -> префикс `dd`).
- Значения `dc_ip`, `secret` и пул доменов могут меняться через UI TgWsProxy/NetFix — при изменении обновить этот снимок.
