# packaging/debian

Сборка `.deb`-пакета LinFix — графической панели (GTK3) с CLI.

## Сборка

```sh
make deb
# → dist/linfix_<version>_all.deb
```

Скрипт `build_deb.sh` собирает пакет через `dpkg-deb` (без debhelper):

- Python-пакет → `/usr/lib/linfix` (обёртки добавляют его в `PYTHONPATH`);
- `/usr/bin/linfix`, `/usr/bin/linfix-gui`;
- `/usr/libexec/linfix-helper` + polkit policy;
- `.desktop`-файл, иконка, systemd user-юниты.

## Установка

```sh
sudo apt install ./dist/linfix_0.5.38_all.deb
```

Зависимости: `python3-gi`, `gir1.2-gtk-3.0`, `nftables`, `curl`,
`ca-certificates`, `git`, `pkexec`, `systemd`.

После установки: запустить **LinFix** из меню → вкладка **«Мастер настройки»** →
«Далее» на каждом шаге. Пароль sudo/polkit запрашивается только на
привилегированных шагах (установка zapret2/TgWsProxy, домены, автозапуск).
