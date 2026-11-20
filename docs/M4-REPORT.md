# M4 — отчёт о выполнении

Дата: 20.11.2026.

## Что сделано

1. **Профили сетей** (`src/linfix/profiles.py`):
   - формат `/etc/linfix/profiles.toml` из SPEC 13.2:
     `[profiles."<uuid>"] name = "Домашний Wi-Fi" strategy = "general"`.
   - `load_profiles`, `save_profiles` (атомарный запись 0644), `set_profile`,
     `clear_profile`, `resolve_profile` (по умолчанию `general`).
   - парсинг `nmcli -t -f UUID,NAME,TYPE connection show --active`.
2. **Helper**: действия `profile.set`, `profile.clear`, `profile.apply`
   (белый список, валидация UUID/name/strategy; `profile.apply` ищет профиль
   по UUID и по умолчанию применяет `general`).
3. **CLI**:
   - `linfix profile list` — UUID, профиль, стратегия, активное соединение.
   - `linfix profile set <uuid> --name X` (helper, root).
   - `linfix profile clear` — сброс профиля активного соединения.
   - `linfix profile set/clear` без root/helper возвращают код 4 с честным
     сообщением.
4. **Диспетчер NM** (`data/networkmanager/90-linfix`): логирование UUID и
   вызов helper для применения профиля.
5. **163 unit-теста**, все проходят.

## Проверки

- `linfix profile list`: `профили не заданы, текущий профиль — general`, exit 0.
- `linfix profile set` без root: exit 4 (`linfix-helper не установлен...`).
- Парсинг nmcli покрыт тестами; смена стратегии запрещена, всегда `general`.
- Тесты сценария: set → load, повторный set без изменений, clear, save format.

## Что не делалось / ограничения

- **Смена стратегий без перезапуска zapret** — MVP не предусмотрен (SPEC: "опционально, M4+"). `STRATEGIES = {"general"}`; структура файла готова для будущих `strategy = "cf"` / `"direct"` / `"auto"`.
- Реальные правки `/etc/linfix/profiles.toml` — требуют root/helper (песочница агента).
- Реальное поведение `profile.apply` на роуминге — после запуска helper: состав проверен юнит-тестами, на стыке NetworkManager будет проверка в `tests/`/перед включением в репозиторий.
