# LinFix Linux

CLI-утилита, которая превращает связку **zapret + TgWsProxy** в управляемый продукт:
находит установленный zapret, держит CF-домены в его hostlist, ставит и запускает
TgWsProxy, диагностирует поломки и защищает сеть от «шторма соединений».

Полное ТЗ — в [SPEC.md](SPEC.md). Факты о целевой машине — в
[docs/INVESTIGATION.md](docs/INVESTIGATION.md), принятые решения — в
[docs/DECISIONS.md](docs/DECISIONS.md), отчёты по этапам — в
`docs/M0-REPORT.md`, `docs/M1-REPORT.md`, `docs/M2-REPORT.md`,
`docs/M3-REPORT.md`, `docs/M4-REPORT.md`, `docs/DESKTOP-ICONS.md`.

Проект переименован в **LinFix** (пакет `linfix`, команда `linfix`);
[docs/DESKTOP-ICONS.md](docs/DESKTOP-ICONS.md) описывает ярлышки сервисов.
В SPEC слово «LinFix» — основное название продукта (Linux-реализация — LinFix).

## Статус

- Реализованы **M0–M4** + переименование + ярлышки сервисов + **M6 (GTK3-панель)**,
  подробности: `docs/M0-REPORT.md`, `docs/M1-REPORT.md`, `docs/M2-REPORT.md`,
  `docs/M3-REPORT.md`, `docs/M4-REPORT.md`, `docs/M6-REPORT.md`.
- **M5 (.deb)** — реализован: `make deb` → `dist/linfix_<version>_all.deb`,
  установка `sudo apt install ./dist/linfix_0.1.1_all.deb` (см. `packaging/debian/README.md`).
- CI: `.github/workflows/build-deb.yml` собирает `.deb` на push в `main` и
  прикрепляет его к GitHub Release по тегу `v*`.

M6 (GTK3-панель): `make gui` или `linfix-gui` (после установки пакета); панель использует `linfix.api`.

Реализованы **M0–M4**:

- `linfix status` с реальным определением zapret/zapret2, hostlist и state;
- `linfix doctor` (+ `--json`): служба, процесс, nft, домены, VPN, TgWsProxy;
- `linfix domains list|test|update`;
- `domains update`: GitHub → декодер → merge → state → рестарт → проверка →
  откат при падении; есть `--dry-run`, `--offline`, `--force`, `--json`;
- `/var/lib/linfix/state.json` хранит список доменов, хэш и версию TgWsProxy;
- `linfix tgws install|config|link|start|stop|restart|status`:
  HTTPS + SHA256, `/opt/tgwsproxy`, user-unit, безопасный конфиг;
- `linfix guard check|status|on|off` — защита от шторма соединений;
- `linfix setup` и `linfix update` — оркестрация настройки/обновлений;
- `linfix profile list|set|clear` — профили сетей (матчинг по UUID, по умолчанию general);
- идемпотентный merge hostlist с сохранением CRLF и бэкапом;
- `linfix-helper` с белым списком действий и валидацией путей;
- unit-тесты (stdlib `unittest`, совместимы с pytest).

Профили сетей реализованы; пакет `.deb` собирается через `make deb` (M5).

## Установка из .deb (для пользователя)

```bash
make deb                              # собрать пакет
sudo apt install ./dist/linfix_0.1.1_all.deb
linfix-gui                            # или запустить LinFix из меню
```

Далее — вкладка **«Мастер настройки»** и «Далее» по шагам:
проверка → zapret2 из GitHub → TgWsProxy из GitHub → CF-домены → автозапуск.
Пароль polkit/sudo запрашивается только на привилегированных шагах.

## Применение доменов

```bash
linfix domains update --dry-run   # показать план, ничего не менять
linfix domains update             # нужен root: helper через pkexec/sudo
linfix domains update --offline   # использовать state/fallback без GitHub
```

При изменении hostlist helper делает бэкап, перезапускает zapret, проверяет
`active` и живой `nfqws`/`nfqws2`. Если проверка не прошла — hostlist
откатывается из бэкапа, а причина пишется в state и выводится в CLI.

## Запуск без установки

```bash
PYTHONPATH=src python3 -m linfix.cli --help
PYTHONPATH=src python3 -m linfix.cli status
PYTHONPATH=src python3 -m linfix.cli doctor
```

## GTK3-панель (M6, через PyGObject)

```bash
make gui        # PYTHONPATH=src python3 -m linfix.gtk.main
```

Панель: `linfix-gui` (при установке пакета), или из меню «LinFix»
(`~/.local/share/applications/linfix.desktop`). Панель строится только поверх
`linfix.api`; привилегированные действия — через `pkexec /usr/libexec/linfix-helper`.

Тесты на панель: `tests/test_gtk.py` (установленGTK3 + трей).

## Тесты

В этой системе нет `pip`/`pytest`, поэтому тесты написаны на stdlib `unittest`
и одновременно совместимы с pytest:

```bash
make test
# или
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

Сейчас: **169 тестов**, все проходят.

## Безопасность

- Перед любой правкой `/opt/zapret2/config` и hostlist — снимок с меткой времени
  (`scripts/backup-zapret.sh`, либо `linfix.util.backup_file`).
- Привилегированные действия — только через `linfix-helper` с белым списком
  действий и канонизацией путей (никакого `shell=True`).
- `linfix` не создаёт собственных nftables-правил: он управляет только тем,
  что создал установщик zapret.

## Требования к графической сессии

TgWsProxy — tray-приложение (AppIndicator + Tk), поэтому его user-юнит
запускается в графической сессии и получает `DISPLAY`. На headless-сервере
без X11 прокси не стартует: нужен X-сервер (например, `Xvfb :99`).
