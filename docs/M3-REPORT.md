# M3 — отчёт о выполнении

Дата: 20.11.2026.

## Что сделано

1. **Guard** (`src/netfix/guard.py`):
   - инкрементальное чтение `proxy.log` по offset/inode
     (`~/.local/state/netfix/guard.json`), без парсинга всего файла;
   - подсчёт `CF proxy failed` за тик;
   - при `failures >= error_threshold` — TLS-тест CF-доменов;
   - остановка `tgwsproxy` только если ошибки **и** TLS-тест провален;
   - событие срабатывания — `~/.local/state/netfix/guard-state.json`;
   - `netfix guard check` (есть `--no-stop`), `guard status`, `guard on|off`.
2. **Doctor**:
   - TLS-выборка CF-доменов (`--no-tls` для отключения);
   - проверка соединений Telegram на `127.0.0.1:1080`;
   - проверка состояния guard (последнее срабатывание);
   - doctor на живой машине занимает ~1.2 с.
3. **`netfix guard on|off`**:
   - helper `config.guard` правит только `[guard] enabled` в
     `/etc/netfix/config.toml`, с бэкапом и валидацией TOML.
4. **Оркестрация**:
   - `netfix setup [--yes] [--dry-run]` — проверяет zapret, домены,
     TgWsProxy, затем запускает doctor;
   - `netfix update [--check] [--dry-run] [--yes]` — сравнивает версию
     TgWsProxy и свежесть доменов, выполняет обновление.
5. **User-timer для guard**:
   - `data/systemd/user/netfix-guard.service`;
   - `data/systemd/user/netfix-guard.timer` (каждые 60 с).
6. **144 unit-теста**, все проходят.

## Проверки

Guard на синтетическом логе (25 ошибок):

```text
$ netfix guard check --no-stop
WARN: 25 ошибок за окно, но TLS-тест проходит — прокси не останавливаем
```

Это правильное поведение: в текущей сети CF-домены доступны, поэтому прокси
не останавливается. Сценарий мёртвого CF-пути проверен unit-тестом с
подменённым TLS-тестом:

```text
status: tripped, stopped: True, failures: 25
event: ~/.local/state/netfix/guard-state.json
```

Doctor:

```text
CF-домены: TLS-тест     PASS    4/4
Telegram подключён      WARN    нет соединений на 127.0.0.1:1080
Guard                   PASS    включён, срабатываний не было
```

## Что не делалось

- Автоматическая установка guard-таймера: файлы юнитов лежат в `data/`,
  установка будет в M5 (пакет). Сейчас можно скопировать вручную:
  `cp data/systemd/user/netfix-guard.* ~/.config/systemd/user/ &&
  systemctl --user daemon-reload && systemctl --user enable --now netfix-guard.timer`
  (при установленном `netfix` в `/usr/bin`).
- Проверка живого Telegram и реальная остановка прокси — нет установленного
  TgWsProxy и root в песочнице.
- Прямые WS к DC и «лавина в journald» как отдельные проверки doctor — guard
  покрывает основную защиту, остальное можно добавить в M3+.
