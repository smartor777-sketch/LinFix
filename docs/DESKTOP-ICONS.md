# Ярлык LinFix

В меню приложений — **одна** запись: `linfix.desktop`, она запускает панель
управления.

```
[Desktop Entry]
Type=Application
Name=LinFix
Exec=linfix-gui
Icon=/usr/share/icons/hicolor/scalable/apps/linfix.svg
Comment=LinFix: панель управления zapret2 + TgWsProxy
Terminal=false
Categories=Network;
StartupNotify=true
```

Все действия (включение/остановка служб, guard, домены, логи) выполняются
внутри панели — отдельные ярлыки «включить/выключить» не нужны.

## История

В ранних версиях (M0–M4, до появления GTK-панели) в меню создавались
6 ярлыков в стиле Zapret2: `linfix-zapret2-on/off`, `linfix-tgwsproxy-on/off`,
`linfix-guard-on/off`. Они выполняли `pkexec systemctl …` напрямую.

После появления панели эти ярлыки удалены: они дублировали кнопки
в «Настройках», требовали отдельных подтверждений и не показывали результат.
Если они остались в системе от старой версии, удалите их:

```bash
rm -f ~/.local/share/applications/linfix-{zapret2,tgwsproxy,guard}-{on,off}.desktop
update-desktop-database -q ~/.local/share/applications
```

## Иконка

- в репозитории: `src/linfix/data/icons/linfix.svg`
- в системе: `/usr/share/icons/hicolor/scalable/apps/linfix.svg` (пакет)
