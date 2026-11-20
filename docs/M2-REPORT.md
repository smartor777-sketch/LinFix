# M2 — отчёт о выполнении

Дата: 20.11.2026.

## Что сделано

1. **GitHub release** (`src/netfix/tgws.py`):
   - `releases/latest` или пиннинг `--version vX.Y.Z`;
   - выбор только `TgWsProxy_linux_amd64` (`.deb`/`.rpm` не используются);
   - обязательный `asset.digest` вида `sha256:...`.
2. **Безопасная установка**:
   - скачивание только по HTTPS, с проверкой конечного URL после редиректа;
   - потоковая загрузка во временный файл, лимит 200 МБ;
   - SHA256 сверяется до установки, при несовпадении старый бинарник не трогается;
   - атомарная установка в `/opt/tgwsproxy/TgWsProxy`, режим 0755;
   - выполняется helper'ом `tgws.install` (root), state обновляется версией и хэшем.
3. **Конфиг TgWsProxy**:
   - поиск `~/.config/TgWsProxy/config.json` и
     `~/.local/share/TgWsProxy/config.json`;
   - создание конфига с секретом `secrets.token_hex(16)` (32 hex), режим 0600;
   - безопасное изменение только разрешённых ключей (`host`, `port`, `cfproxy`,
     `h2`, `verbose`, `pool_size`), с бэкапом и валидацией;
   - `host` в MVP — только loopback;
   - секрет никогда не печатается.
4. **User-служба**:
   - `~/.config/systemd/user/tgwsproxy.service` из шаблона;
   - идемпотентная установка, `daemon-reload`, `enable`, `start`;
   - `tgws start|stop|restart|status`.
5. **CLI**:
   - `tgws install [--version X] [--dry-run] [--force] [--no-config]
     [--no-unit] [--no-start] [--json]`;
   - `tgws config [--port] [--host] [--pool-size] [--verbose on|off]
     [--h2 on|off] [--cfproxy on|off] [--create] [--json]`;
   - `tgws link [--qr]`;
   - `status` показывает версию TgWsProxy из state.
6. **114 unit-тестов**, все проходят.

## Проверки

Живой dry-run релиза:

```text
$ netfix tgws install --dry-run
Версия:  v1.11.4
Asset:   TgWsProxy_linux_amd64 (57377032 байт)
SHA256:  60df0ce2fab434a57ed8c72912e4570642bce74d1a456ebd8aca911a73fd4a5d
Куда:    /opt/tgwsproxy/TgWsProxy
Статус:  будет установлено
```

Конфиг в изолированном `HOME`:

```text
$ netfix tgws config --port 1081 --verbose on
Конфиг: .../TgWsProxy/config.json
  host = 127.0.0.1
  port = 1081
  cfproxy = True
  h2 = True
  verbose = True
  pool_size = 4
```

Права `0600`, секрет 32 hex, `tgws link` печатает `tg://proxy?...&secret=dd…`.

## Что не делалось

- Реальная установка 57 МБ бинарника: в песочнице агента нет root, helper
  требует root. Проверены dry-run, парсинг release, SHA256-логика, конфиг,
  unit и CLI-путь с фейковым helper.
- Не подтверждены на живой установке:
  - точный путь конфига, который создаёт сам TgWsProxy;
  - наличие headless-режима и поведение без DISPLAY;
  - имя лог-файла.
  Это надо проверить сразу после первого `sudo netfix tgws install`
  (`--help`, `lsof -p`, `ls` вокруг конфига). При расхождении — поправить
  `CONFIG_CANDIDATES`/`LOG_CANDIDATES` и unit.

## Как поставить на целевой машине

```bash
scripts/backup-zapret.sh
netfix tgws install --dry-run
sudo netfix tgws install          # или через установленный helper/pkexec
netfix tgws status
netfix tgws link
```

Если служба не стартует в headless-сессии — повторить с `--no-start`, затем
запустить вручную из графической сессии:

```bash
systemctl --user start tgwsproxy
journalctl --user -u tgwsproxy -n 50 --no-pager
```
