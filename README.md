# NetFix Linux

CLI-утилита, которая превращает связку **zapret + TgWsProxy** в управляемый продукт:
находит установленный zapret, держит CF-домены в его hostlist, ставит и запускает
TgWsProxy, диагностирует поломки и защищает сеть от «шторма соединений».

Полное ТЗ — в [SPEC.md](SPEC.md). Факты о целевой машине — в
[docs/INVESTIGATION.md](docs/INVESTIGATION.md), принятые решения — в
[docs/DECISIONS.md](docs/DECISIONS.md), отчёты по этапам — в
`docs/M0-REPORT.md`, `docs/M1-REPORT.md`, `docs/M2-REPORT.md`,
`docs/M3-REPORT.md`.

## Статус

Реализованы **M0–M3**:

- `netfix status` с реальным определением zapret/zapret2, hostlist и state;
- `netfix doctor` (+ `--json`): служба, процесс, nft, домены, VPN, TgWsProxy;
- `netfix domains list|test|update`;
- `domains update`: GitHub → декодер → merge → state → рестарт → проверка →
  откат при падении; есть `--dry-run`, `--offline`, `--force`, `--json`;
- `/var/lib/netfix/state.json` хранит список доменов, хэш и версию TgWsProxy;
- `netfix tgws install|config|link|start|stop|restart|status`:
  HTTPS + SHA256, `/opt/tgwsproxy`, user-unit, безопасный конфиг;
- `netfix guard check|status|on|off` — защита от шторма соединений;
- `netfix setup` и `netfix update` — оркестрация настройки/обновлений;
- идемпотентный merge hostlist с сохранением CRLF и бэкапом;
- `netfix-helper` с белым списком действий и валидацией путей;
- unit-тесты (stdlib `unittest`, совместимы с pytest).

Профили сетей, пакет `.deb` и GTK — следующие этапы (M4–M6).

## Применение доменов

```bash
netfix domains update --dry-run   # показать план, ничего не менять
netfix domains update             # нужен root: helper через pkexec/sudo
netfix domains update --offline   # использовать state/fallback без GitHub
```

При изменении hostlist helper делает бэкап, перезапускает zapret, проверяет
`active` и живой `nfqws`/`nfqws2`. Если проверка не прошла — hostlist
откатывается из бэкапа, а причина пишется в state и выводится в CLI.

## Запуск без установки

```bash
PYTHONPATH=src python3 -m netfix.cli --help
PYTHONPATH=src python3 -m netfix.cli status
PYTHONPATH=src python3 -m netfix.cli doctor
```

## Тесты

В этой системе нет `pip`/`pytest`, поэтому тесты написаны на stdlib `unittest`
и одновременно совместимы с pytest:

```bash
make test
# или
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

Сейчас: **144 теста**, все проходят.

## Безопасность

- Перед любой правкой `/opt/zapret2/config` и hostlist — снимок с меткой времени
  (`scripts/backup-zapret.sh`, либо `netfix.util.backup_file`).
- Привилегированные действия — только через `netfix-helper` с белым списком
  действий и канонизацией путей (никакого `shell=True`).
- `netfix` не создаёт собственных nftables-правил: он управляет только тем,
  что создал установщик zapret.
