# M0 — отчёт о выполнении

Дата: 20.11.2026.

## Что сделано

1. **Исследование** фактической установки zapret — `docs/INVESTIGATION.md`.
   Ключевое: на машине **zapret2** (`/opt/zapret2`, `nfqws2`, `zapret2.service`),
   hostlist — `/opt/zapret2/ipset/zapret-hosts-user.txt` (29 756 строк, CRLF),
   CF-доменов в нём 0/20.
2. **Каркас проекта** по SPEC 5.3: `pyproject.toml`, `src/netfix/`,
   `data/`, `packaging/debian/`, `docs/`, `tests/`, `scripts/`.
3. **Реализовано:**
   - `netfix --help`, `netfix status` с реальным детектом zapret/zapret2;
   - `netfix doctor` (базовый набор проверок, `--json`);
   - `netfix domains list` и `netfix domains test` (только чтение);
   - `netfix logs`, `netfix tgws status|link`;
   - декодер CF-доменов, fallback-список, HTTPS-загрузка;
   - идемпотентный merge hostlist с сохранением CRLF и бэкапом;
   - `netfix-helper` с белым списком действий и валидацией путей;
   - `api.py` для будущего GUI.
4. **Тесты:** 77 unit-тестов на stdlib `unittest` (совместимы с pytest), все
   проходят.

## Живая проверка

```text
$ netfix status
Zapret:       zapret2 — active (enabled), pid 1192
Установка:    /opt/zapret2 (nfqws2)
Очередь:      qnum 300, fwmark 0x40000000, user tpws
Hostlist:     /opt/zapret2/ipset/zapret-hosts-user.txt
CF-домены:    0/20 в hostlist
TgWsProxy:    не установлен
Порт 1080:    свободен
```

Merge проверен **на копии** реального hostlist в `/tmp`, без правок `/opt`:

- добавлено 20 доменов;
- CRLF сохранён (29 777 CRLF, 0 одиночных LF);
- повторный запуск: `changed=False`, новый бэкап не создан;
- создан ровно один бэкап `.bak-netfix-…`.

## Что НЕ делалось

- `/opt/zapret2` и `zapret2.service` **не изменялись**.
- Живой `nft list ruleset` не снят: в песочнице агента `sudo` запрещён
  (`no_new_privs`). Правила восстановлены по журналу и `common/nft.sh`.
- Установка TgWsProxy, guard, профили, GTK — M2–M6.

## Baseline-бэкап

`backups/zapret-baseline-20261120-043603/` — config + hostlist + exclude + rut
+ MANIFEST с sha256. Скрипт для повторного снимка: `scripts/backup-zapret.sh`.

## Следующий шаг (M1)

1. `zapret.py`: завершить detect (учтён zapret2), добавить `state.json`.
2. `domains update`: fetch → decode → merge через helper → restart с откатом.
3. Проверить на живой машине: добавить домены → TLS-тест; убрать → FAIL.
4. Учесть, что в текущей сети CF-домены не блокируются: TLS-тест зелёный
   даже без zapret. Для приёмки нужна сеть с реальной SNI-блокировкой.
