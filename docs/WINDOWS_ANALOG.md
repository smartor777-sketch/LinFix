# Заметки: возможный аналог LinFix для Windows

Дата: 2026-11-27  
Статус: черновик для обсуждения, не решение и не план реализации.

## Контекст

Вопрос: можно ли сделать на Windows аналог LinFix — с такой же минималистичной панелью управления, но с логикой внутри, похожей на LinFix.

Короткий ответ: **да, но это не прямой порт**. Реалистичный путь — переиспользовать идеи, форматы и часть чистой логики LinFix, а системный слой и GUI под Windows написать заново. Полноценный аналог по глубине возможностей — отдельный продукт.

## Что есть сейчас

### LinFix (этот репозиторий)

- Стек: Python 3, GTK3, Linux.
- Назначение: GUI-панель вокруг `zapret2`/`nfqws2` и `TgWsProxy`.
- Размер кода: ~16.6k строк в `src/linfix` (включая GTK-экраны).
- Ключевые Linux-зависимости:
  - `helper.py` — привилегированный one-shot helper, белый список действий, валидация путей, ~1484 строк.
  - Эскалация прав: `pkexec`/polkit, `sudo`, установленный `/usr/libexec/linfix-helper`.
  - Службы: `systemctl` (system и `--user`).
  - Zapret: обнаружение через `/proc`, `nfqws/nfqws2`, nftables/NFQUEUE/fwmark/qnum, установка в `/opt/zapret2`.
  - TgWsProxy: бинарник в `/opt/tgwsproxy`, user-systemd unit, конфиг в `~/.config/TgWsProxy/config.json`.
  - Пути/состояние: `/etc/linfix`, `/var/lib/linfix`, `~/.config/linfix`.
- Недавнее изменение: проверка обновлений переведена с `api.github.com` на обычные страницы GitHub (`src/linfix/github_web.py`), чтобы не упираться в лимит 60 анонимных запросов/час на IP.

### NetFix (внешний референс)

Репозиторий: https://github.com/rupleide/NetFix  
Осмотрено: 2026-11-27, `gh repo view` + клонирование в `/tmp` для чтения кода.

- Стек: C# WPF, .NET 8, Windows 10/11 x64.
- Лицензия: GPL-3.0.
- Назначение: GUI для обхода блокировок, вокруг Zapret/WinDivert и TgWsProxy.
- Масштаб: ~41k строк `.cs/.xaml` (грубо, включая большой `MainWindow.xaml.cs` ~20k строк).
- Признаки зрелого Windows-продукта:
  - Установка/обновление компонентов Zapret и TgWsProxy (`AutoDownloadService.cs`, `ComponentVersionService.cs`).
  - Работа с процессами/службами Windows, `service.bat`, `C:\Zapret`, `winws.exe`, `TgWsProxy.exe`.
  - Диагностика, автонастройка, трей, обновление приложения.
  - Проверка обновлений: есть web-fallback через redirect/expanded assets в `UpdateService.cs`, но в других местах (`AutoDownloadService.cs`, `ComponentVersionService.cs`, `AutoSetupService.cs`, `MainWindow.xaml.cs`) всё ещё используются запросы к `api.github.com`.

Вывод: NetFix — уже существующий Windows-аналог по проблеме. LinFix и NetFix решают похожую задачу, но на разных платформах и с разными архитектурными компромиссами.

## Что можно переиспользовать из LinFix

| Область | Переносимость | Комментарий |
|---|---:|---|
| `github_web.py` | Высокая | Парсинг релизов GitHub без REST API. Полезно и для Linux, и для Windows. |
| `cfdomains.py`, `hostlist.py` | Высокая | Нормализация доменов, merge/import hostlist, работа со списками. |
| `config.py`, `state.py` | Средняя/высокая | Форматы и состояние можно сохранить, пути и дефолты нужно адаптировать. |
| `sitecheck.py`, `proxyprobe.py` | Средняя | HTTP/TLS/DNS-проверки в целом переносимы; OS-специфику надо отделить. |
| `updates.py` | Средняя | Логика сравнения версий переносима, установка/применение обновлений — разная. |
| `api.py` как контракт | Средняя | Удобно сохранить похожие DTO/коды ответов для GUI, но реализацию переписать. |
| `doctor.py`, `netdiag.py`, `netstate.py` | Низкая/средняя | Часть проверок переносима, но многие признаки Linux-специфичны. |
| `helper.py`, `zapret.py`, `tgws.py` (systemd/install) | Низкая | Это Linux-скелет: pkexec/systemd/nftables/nfqws/пути. На Windows нужна новая реализация. |
| `gtk/*` | Низкая | GTK3 можно теоретически таскать на Windows, но для нормального продукта лучше нативный UI. |

## Что придётся писать заново для Windows

### 1. GUI

- LinFix: GTK3, Notebook/экраны, tray через AppIndicator/GTK.
- Windows-вариант: WPF/WinUI 3/.NET, либо Tauri/Qt/PySide при отдельном обосновании.
- Цель сохранить: минималистичность, понятные статусы, мало визуального шума.
- Не цель: переносить GTK-код 1:1.

### 2. Права и привилегированные действия

- Linux: `pkexec`/polkit, helper с белым списком действий.
- Windows: UAC elevation, отдельный админ-процесс или служба, аккуратный контроль аргументов/путей, ACL.
- Нужно заранее решить: portable + elevation по действию, или установленная служба/агент.

### 3. Zapret на Windows

- Linux LinFix: `zapret2/nfqws2`, nftables/NFQUEUE, systemd, `/opt/zapret2`.
- Windows: `winws.exe`/WinDivert, конфиги/батники/профили, запуск как процесс или служба, правила firewall/driver specifics.
- Это главный источник сложности: поведение и диагностика отличаются от Linux.

### 4. TgWsProxy на Windows

- Плюс: у Flowseal есть Windows-релизы (`TgWsProxy_windows.exe`, в т.ч. варианты под старые/ARM).
- Нужно реализовать: скачивание, проверка sha256, установка, конфиг, автозапуск, старт/стоп/рестарт, обновление без битого состояния.

### 5. Установка, пути, обновления

- Linux: `/opt`, `/var/lib/linfix`, `~/.config/linfix`, `.deb`.
- Windows: `%ProgramFiles%`/`%ProgramData%`/`%APPDATA%`/`%LOCALAPPDATA%`, installer/MSIX/portable.
- Обновление exe: нельзя просто заменить запущенный файл без аккуратной схемы.

### 6. Диагностика

- Linux: systemctl/nft/ip/proc, nfqws-процессы, sysctl, маршруты.
- Windows: службы, процессы, WinDivert, firewall, DNS/TCP/TLS, логи приложений, права администратора.

## Варианты стратегии

### A. Отдельный Windows-клиент, LinFix как референс

Переиспользовать идеи, UX, форматы данных и переносимую логику. GUI и OS-слой новые.

- Плюсы: чистая архитектура, нормальный Windows UX, нет груза GTK/systemd.
- Минусы: больше работы, чем “попробовать запустить как есть”.

### B. Доработка/форк NetFix

Брать готовый Windows-проект и постепенно улучшать.

- Плюсы: WinDivert/winws/TgWsProxy/installer-реальность уже обкатаны; быстрее до рабочего Windows-результата.
- Минусы: лицензия GPL-3.0; архитектура/UX могут не совпадать с желаемым минимализмом LinFix; большой монолитный UI.

### C. Попытка портировать текущий Python/GTK LinFix

Технически возможно частично, но практический смысл слабый.

- Плюсы: может переиспользоваться часть Python.
- Минусы: GTK на Windows, права, службы, zapret-winws, упаковка — всё равно переписывать; получится гибрид с плохой поддерживаемостью.

## Рекомендация

Для цели “такая же минималистичная панель, но Windows” разумнее всего:

1. **Не портировать Linux-скелет LinFix.**
2. **Выделить/переиспользовать переносимое ядро**: проверка обновлений без GitHub API, домены/hostlist, конфиг/состояние, проверки сайтов.
3. **Сделать новый Windows system-слой**: установка/запуск/обновление TgWsProxy и winws, права, автозапуск, диагностика.
4. **Сделать новый минималистичный Windows GUI**.
5. **NetFix использовать как референс поведения на Windows**, но учитывать лицензию: идеи и наблюдения — ок, копирование GPL-кода в MIT-проект — нельзя без совместимости лицензий.

## Грубая оценка трудозатрат

Очень приблизительно, для одного опытного разработчика:

| Объём | Что входит | Оценка |
|---|---|---:|
| MVP | Минималистичная панель, установка/запуск TgWsProxy, проверка обновлений без API-лимита, импорт/обновление доменов, простая диагностика, логи | 2–5 недель |
| Расширенный MVP | + winws/zapret-профили, базовая автонастройка, установщик, автозапуск, аккуратный UAC | 1–2 месяца |
| Полноценный аналог по смыслу | + глубокая диагностика, проверка стратегий, безопасные обновления, восстановление, полировка UX, поддержка edge cases | 1–3 месяца |

Оценка зависит от того, нужен ли только TgWsProxy или полноценный zapret/winws стек.

## Идея архитектуры для MVP

```text
core/               # переносимая логика
  config/
  state/
  domains/
  updates/          # github_web-подход, без лимита API
  checks/           # DNS/TLS/HTTP/site checks

windows/            # Windows-специфика
  elevation/        # UAC, admin actions
  services/         # процессы/службы/автозапуск
  tgws/             # install/config/start/stop/update
  zapret/           # winws/service.bat/config, позже
  paths/

ui/                 # минималистичная панель
  Status
  Domains
  Diagnostics
  Settings
  Logs
```

## Практичные первые шаги, если начнём

1. Определить границы MVP: только TgWsProxy или сразу TgWsProxy + winws/zapret.
2. Выбрать стек Windows GUI: WPF/WinUI 3 предпочтительнее, если нужен нативный Windows UX.
3. Определить модель прав: portable + UAC или установленный сервис/агент.
4. Вынести переносимое ядро из LinFix в отдельный пакет/папку без Linux-специфики.
5. Переиспользовать подход `github_web.py` для обновлений сразу, чтобы не повторить проблему лимита GitHub API.
6. Сделать маленький вертикальный срез: Status + установка TgWsProxy + проверка обновлений + лог.

## Риски и нюансы

- **Лицензии**: LinFix — MIT, NetFix — GPL-3.0. Нельзя копировать код NetFix в MIT-проект без соблюдения условий GPL.
- **WinDivert/антивирусы**: на Windows это ожидаемая зона трения; нужны понятные сообщения и аккуратная установка.
- **Обновления запущенных компонентов**: замена exe/перезапуск службы должны быть атомарными и восстанавливаемыми.
- **Сложность zapret-winws**: основная не в UI, а в надёжной установке, диагностике и подборе рабочих стратегий.
- **Дрейф продукта**: NetFix уже большой; если делать “LinFix для Windows”, важно держать минималистичный scope, а не копировать все функции.

## Источники, использованные при подготовке заметки

- Локальный код LinFix: `README.md`, `pyproject.toml`, `src/linfix/helper.py`, `src/linfix/tgws.py`, `src/linfix/zapret.py`, `src/linfix/api.py`, `src/linfix/util.py`, `src/linfix/gtk/*`, `src/linfix/github_web.py`.
- NetFix: https://github.com/rupleide/NetFix
  - `README.md`
  - `NetFix.csproj`
  - `Services/UpdateService.cs`
  - `Services/AutoDownloadService.cs`
  - `Services/ComponentVersionService.cs`
  - `Services/AutoSetupService.cs`
  - `Services/ZapretConfigService.cs`
  - `Services/TgWsProxySettingsService.cs`
  - `Services/DiagnosticsEngine.cs`
