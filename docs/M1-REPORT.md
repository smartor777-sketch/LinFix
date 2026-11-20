# M1 — отчёт о выполнении

Дата: 20.11.2026.

## Что сделано

1. **`/var/lib/linfix/state.json`** (`src/linfix/state.py`):
   - дата обновления, sha256, количество и **сам список** CF-доменов;
   - hostlist и служба, последняя ошибка;
   - атомарная запись 0644 (пишет только helper).
2. **`linfix domains update`**:
   - GitHub → decode → сравнение с hostlist;
   - `--dry-run` — показать план без правок;
   - `--offline` — использовать state/fallback;
   - `--force` — применить/обновить state, даже если доменов не добавилось;
   - `--json` — стабильный ответ для GUI.
3. **Транзакция `linfix-helper domains.apply`**:
   - валидация hostlist/state/service/доменов/sha256/HTTPS;
   - merge с бэкапом;
   - запись state;
   - рестарт zapret (если hostlist изменился);
   - проверка `systemctl is-active` и живого `nfqws`/`nfqws2`;
   - при провале — откат hostlist из бэкапа и повторный рестарт;
   - журнал и причина ошибки — в JSON и state.
4. **CLI-интеграция**:
   - `status` показывает дату/хэш state и последнюю ошибку;
   - `domains list` показывает state и умеет `--json`;
   - `doctor` использует домены из state и проверяет свежесть/ошибки state.
5. **94 unit-теста**, все проходят.

## Проверки

Безопасный прогон на живой машине:

```text
$ linfix domains update --dry-run
Источник: github, доменов: 20
Hostlist: /opt/zapret2/ipset/zapret-hosts-user.txt
Добавить: 20, уже есть: 0
Digest:   ffdb0f5b99296fc5...
Рестарт:  да
```

SHA256 hostlist до и после — одинаковый: `/opt/zapret2` не изменялся.

Сквозной путь CLI → helper проверен фейковым helper:

- успех: `exit 0`, печать добавленных/бэкапа/рестарта;
- провал: `exit 5`, печать журнала и ошибки, признак откатa.

Транзакция и откат покрыты тестами `tests/test_helper_apply.py`:
успешный apply, повторный apply без рестарта, отключённый рестарт,
провал рестарта с откатом hostlist, несовпадение sha256.

## Что не делалось

- Реальный apply на этой машине: в песочнице агента нет root, а helper
  принципиально требует root. Нужен запуск пользователем:
  `sudo linfix domains update` (или установленный helper через pkexec).
- Живой дамп nftables — по-прежнему требует root.

## Как применить на целевой машине

```bash
scripts/backup-zapret.sh
linfix domains update --dry-run
sudo linfix domains update
systemctl is-active zapret2
pgrep -a nfqws2
linfix domains list
linfix doctor
```

Если рестарт упал, helper сам вернёт hostlist из бэкапа; дополнительно
причина будет в `state.json` (`last_error`) и в `journalctl -u zapret2`.
