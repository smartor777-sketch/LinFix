# NetFix Linux

CLI-утилита, которая превращает связку **zapret + TgWsProxy** в управляемый продукт:
находит установленный zapret, держит CF-домены в его hostlist, ставит и запускает
TgWsProxy, диагностирует поломки и защищает сеть от «шторма соединений».

Полное ТЗ — в [SPEC.md](SPEC.md). Факты о целевой машине — в
[docs/INVESTIGATION.md](docs/INVESTIGATION.md), принятые решения — в
[docs/DECISIONS.md](docs/DECISIONS.md).

## Статус

Реализован **M0** (каркас):

- `netfix --help`, `netfix status` с реальным определением zapret;
- `netfix doctor` (базовый набор проверок);
- `netfix domains list` и `netfix domains test` (только чтение);
- декодер CF-доменов и идемпотентный merge hostlist;
- unit-тесты (stdlib `unittest`, совместимы с pytest).

`domains update`, установка TgWsProxy, guard, профили и GTK — следующие этапы (M1–M6).

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

## Безопасность

- Перед любой правкой `/opt/zapret2/config` и hostlist — снимок с меткой времени
  (`scripts/backup-zapret.sh`, либо `netfix.util.backup_file`).
- Привилегированные действия — только через `netfix-helper` с белым списком
  действий и канонизацией путей (никакого `shell=True`).
- `netfix` не создаёт собственных nftables-правил: он управляет только тем,
  что создал установщик zapret.
