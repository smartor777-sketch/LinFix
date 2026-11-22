# Исследование целевой машины (M0)

- **Дата снятия фактов:** 20.11.2026, 04:33–04:37 MSK
- **Хост:** `<hostname скрыт>`
- **Пользователь:** `user` (uid 1000, группы `adm`, `sudo`, …)
- **Назначение документа:** зафиксировать фактическую установку zapret и
  окружение, чтобы не программировать по предположениям из SPEC.

---

## 0. Главное в одном абзаце

На машине стоит **не классический `bol-van/zapret`, а zapret2**: каталог
`/opt/zapret2`, бинарник `nfqws2` v1.0.6, служба `zapret2.service`, nft-таблица
`inet zapret2`, очередь `qnum 300`. Реальный hostlist, в который нужно писать
CF-домены, — `/opt/zapret2/ipset/zapret-hosts-user.txt` (29 756 строк, **CRLF**).
CF-доменов в нём сейчас **0 из 20**. TgWsProxy не установлен, порт 1080 свободен.
Активен VPN (`vpn0`, split-default), поэтому успешный TLS-тест CF-доменов
**не доказывает** работу zapret.

---

## 1. Как снимались факты и ограничение окружения

Выполнялись команды из SPEC 0/8/18:

```bash
systemctl status zapret --no-pager
systemctl cat zapret2
ps -eo pid,args | grep -E 'nfqws|tpws' | grep -v grep
nft list ruleset
cat /opt/zapret2/config
ls -la /opt/zapret2/ipset
```

**Ограничение:** агент запущен в песочнице с флагом `no_new_privs`, поэтому
`sudo` не работает:

```
$ sudo -n true
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
$ nft list ruleset
Operation not permitted (you must be root)
```

Следствие: живой дамп nftables агентом не получен. Правила восстановлены по
`journalctl -u zapret2` и по коду `/opt/zapret2/common/nft.sh`. Перед первой
правкой это нужно один раз подтвердить вручную (команды в разделе 12).
Чтение `/opt/zapret2/*` и `journalctl` доступно обычному пользователю.

---

## 2. Система

| Параметр | Значение |
|---|---|
| Дистрибутив | Linux Mint 22.3 (Zena), `ID_LIKE="ubuntu debian"` |
| Ядро | `7.0.0-43-generic` |
| Python | 3.12.3 (`/usr/bin/python3`) |
| init | systemd |
| Firewall | nftables (`nft` есть, `/usr/sbin/nft`) |
| Журнал | `journalctl` читается пользователем (группа `adm`) |
| VPN | активен `vpn0` (tun), split-default — см. раздел 8 |

`/etc/nftables.conf` содержит только пустую таблицу `inet filter`
(hook'и input/forward/output без правил). Правила zapret2 создаются во время
запуска службы в отдельной таблице.

---

## 3. Zapret: факты

| Параметр | Фактическое значение |
|---|---|
| Проект | **zapret2** (`nfqws2 --version` → `v1.0.6 (221d7dff…) lua_compat_ver 6`) |
| Каталог | `/opt/zapret2` (владелец root:root) |
| Бинарник | `/opt/zapret2/nfq2/nfqws2` |
| Служба | `zapret2.service` — **active**, **enabled** |
| Юнит | `/usr/lib/systemd/system/zapret2.service` |
| Тип службы | `Type=forking`, `GuessMainPID=no` |
| ExecStart | `/opt/zapret2/init.d/sysv/zapret2 start` |
| ExecStop | `/opt/zapret2/init.d/sysv/zapret2 stop` |
| MainPID | `0` (не использовать! pid искать через ps/`/proc`) |
| Процесс | pid **1192**, `/opt/zapret2/nfq2/nfqws2` |
| Пользователь nfqws | `tpws` (`--user=tpws`) |
| Очередь | `--qnum=300` |
| fwmark | `--fwmark=0x40000000` |
| nft-таблица | `inet zapret2` (значение по умолчанию из `common/nft.sh`) |
| Конфиг | `/opt/zapret2/config` |
| Ответы установщика | `/opt/zapret2/answers.txt`: `Y`, `nftables`, `Y`, `hostlist`, `Y` |

### Аргументы процесса (истина для hostlist)

`ps` показывает один процесс `nfqws2` со строкой (сокращено):

```
--user=tpws --fwmark=0x40000000
--lua-init=@/opt/zapret2/lua/zapret-lib.lua
--lua-init=@/opt/zapret2/lua/zapret-antidpi.lua
--lua-init=@/opt/zapret2/lua/zapret-auto.lua
--qnum=300
--filter-tcp=443 --filter-l7=tls /opt/zapret2/ipset/zapret-hosts-rut.txt
  --payload=tls_client_hello --lua-desync=tcpseg:pos=0,1:ip_id=rnd:repeats=1 --new
--filter-tcp=443 --filter-l7=tls
  --hostlist=/opt/zapret2/ipset/zapret-hosts-user.txt
  --hostlist=/opt/zapret2/ipset/zapret-hosts.txt.gz
  --hostlist-exclude=/opt/zapret2/ipset/zapret-hosts-user-exclude.txt
  --payload=tls_client_hello
  --lua-desync=multisplit:pos=1:seqovl=568:seqovl_pattern=t4pda --new
--filter-tcp=80 --filter-l7=http <те же три списка>
  --payload=http_req --lua-desync=http_methodeol --new
--filter-udp=443 --filter-l7=quic <те же три списка>
  --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=6
```

### Конфиг `/opt/zapret2/config` (значимые строки)

```ini
NFQWS2_ENABLE=1
NFQWS2_PORTS_TCP=80,443
NFQWS2_PORTS_UDP=443
NFQWS2_TCP_PKT_OUT=20
NFQWS2_TCP_PKT_IN=10
NFQWS2_UDP_PKT_OUT=5
NFQWS2_UDP_PKT_IN=3
MODE_FILTER=hostlist
DESYNC_MARK=0x40000000
DESYNC_MARK_POSTNAT=0x20000000
GZIP_LISTS=1

NFQWS2_OPT="
--filter-tcp=443 --filter-l7=tls /opt/zapret2/ipset/zapret-hosts-rut.txt --payload=tls_client_hello --lua-desync=tcpseg:pos=0,1:ip_id=rnd:repeats=1 --new
--filter-tcp=443 --filter-l7=tls <HOSTLIST> --payload=tls_client_hello --lua-desync=multisplit:pos=1:seqovl=568:seqovl_pattern=t4pda --new
--filter-tcp=80 --filter-l7=http <HOSTLIST> --payload=http_req --lua-desync=http_methodeol --new
--filter-udp=443 --filter-l7=quic <HOSTLIST_NOAUTO> --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=6
"
```

Важно: `<HOSTLIST>` — плейсхолдер init-скрипта. Он разворачивается в три
аргумента `--hostlist` / `--hostlist-exclude`, которые видны в `ps`.
**Писать домены нужно в `--hostlist`, а не в конфиг.**

---

## 4. nftables

Живой `nft list ruleset` агенту недоступен (нет root), но журнал запуска
фиксирует создание правил:

```
Inserting nftables ipv4 rule for nfqws postrouting (qnum 300) : tcp dport {80,443} ct original packets 1-20
Inserting nftables ipv4 rule for nfqws prerouting  (qnum 300) : tcp sport {80,443} ct reply    packets 1-10
Inserting nftables ipv4 rule for nfqws postrouting (qnum 300) : udp dport {443}    ct original packets 1-5
Inserting nftables ipv4 rule for nfqws prerouting  (qnum 300) : udp sport {443}    ct reply    packets 1-3
```

- Тип firewall: `nftables` (`answers.txt`, `FWTYPE` определяется в
  `common/fwtype.sh`).
- Таблица: `inet zapret2` (`ZAPRET_NFT_TABLE=zapret2` в `common/nft.sh`).
- Очередь: `300` (`QNUM=${QNUM:-300}` в `init.d/sysv/functions`).
- Правила создаёт сам zapret2 при старте; **LinFix не должен добавлять свои**.

---

## 5. Hostlist: что и где менять

### Реальные списки

| Роль | Путь | Размер | Строк | Формат |
|---|---|---|---|---|
| **User hostlist (цель merge)** | `/opt/zapret2/ipset/zapret-hosts-user.txt` | 903 869 B | 29 756 | ASCII, **CRLF**, root:root 0644 |
| База установщика | `/opt/zapret2/ipset/zapret-hosts.txt.gz` | 645 018 B | — | gzip |
| Исключения | `/opt/zapret2/ipset/zapret-hosts-user-exclude.txt` | 104 B | 9 | LF |
| Отдельный список rutracker | `/opt/zapret2/ipset/zapret-hosts-rut.txt` | 158 B | 10 | LF |

- `zapret-hosts-user.txt` изменён 05.11.2026 01:18, принадлежит root.
- Строки — домены, первая строка `_domainkey.youtube.com`, далее
  `000-255.sentry.io`, … (обычный список блокировок).
- **CF-домены TgWsProxy: 0 из 20.** Проверка:
  `grep -cE 'pclead\.co\.uk|offshor\.co\.uk|cakeisalie\.co\.uk'` → `0`.

### Исключения

```
127.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
100.64.0.0/10
::1
fc00::/7
fe80::/10
```

### Выводы для merge

1. Целевой файл определяется по `--hostlist` процесса, а не по конфигу.
2. Файл большой и с CRLF: переписывать его целиком с LF нельзя — это создаст
   diff на 29 756 строк. Merge обязан сохранять CRLF (см. DECISIONS D3).
3. Бэкап перед первой правкой обязателен (SPEC 8.4/15.5).

---

## 6. Автообновление списков zapret2

Есть таймер:

```
zapret2-list-update.timer  enabled  OnCalendar=*-*-2,4,6,...,30 00:00:00
                           RandomizedDelaySec=86400  Persistent=true
zapret2-list-update.service → /opt/zapret2/ipset/get_config.sh
```

`get_config.sh` при пустом `GETLIST` запускает `get_ipban.sh`. Функция
`getuser()` в `ipset/def.sh` **читает** `zapret-hosts-user.txt` и строит из него
IP-списки, но **не перезаписывает** сам hostlist. То есть таймер не стирает
CF-домены.

Тем не менее риск «обновление затирает списки» остаётся при переустановке
zapret2. Поэтому в M1 нужен `state.json` с нашим источником правды и повторное
применение доменов после обновления (SPEC 10.6, 17).

---

## 7. TgWsProxy

| Параметр | Факт |
|---|---|
| `/opt/tgwsproxy` | отсутствует |
| Процесс `TgWsProxy` | не запущен |
| Порт 1080 | свободен (`ss -ltnp` не показывает слушателя) |
| `~/.config/TgWsProxy` | отсутствует |
| `~/.local/share/TgWsProxy` | отсутствует |

Последний релиз GitHub на 20.11.2026 — **v1.11.4**:

| Asset | Размер | digest (sha256, начало) |
|---|---|---|
| `TgWsProxy_linux_amd64` | 57 377 032 B | `sha256:60df0ce2fab43…` |
| `TgWsProxy_linux_amd64.deb` | 56 557 204 B | `sha256:9b21d9573dc16…` |
| `TgWsProxy_linux_amd64.rpm` | 56 820 150 B | `sha256:d4e3b70ec977f…` |

Для M2: качать raw-бинарник, сверять полный digest из GitHub API, ставить в
`/opt/tgwsproxy/TgWsProxy` (0755). Путь конфига и наличие headless-режима
нужно определить фактически после установки (`--help`, `lsof -p`, `ls`).

---

## 8. Сеть и VPN

```
wlo1   192.168.1.100/24
vpn0   10.8.0.2/32  (tun, mtu 1400)

0.0.0.0/1     dev vpn0  metric 1
default       via 192.168.1.1 dev wlo1 metric 600
10.8.0.0/24   dev vpn0
128.0.0.0/1   dev vpn0  metric 1
```

Это классический **split-default VPN**: маршруты `0.0.0.0/1` и `128.0.0.0/1`
перекрывают обычный default, поэтому почти весь трафик идёт через `vpn0`.
SPEC 3 и 11 требуют предупреждать о таком режиме.

### Проверка TLS до CF-доменов

При включённом VPN (04:35):

```
kws2.pclead.co.uk  OK TLSv1.3 0.44 с
kws4.pclead.co.uk  OK TLSv1.3 0.50 с
```

Доменов при этом **нет** в hostlist.

### VPN — динамическое состояние

Через ~10 минут после первого замера VPN отключился сам:

```
$ ip -brief addr
lo    127.0.0.1/8
wlo1  192.168.1.100/24
# vpn0 исчез, маршрутов 0.0.0.0/1 и 128.0.0.0/1 больше нет
```

Повторный TLS-тест **без VPN и без доменов в hostlist**:

```
kws2.pclead.co.uk  OK TLSv1.3 0.17 с
kws4.pclead.co.uk  OK TLSv1.3 0.17 с
kws2.offshor.co.uk OK TLSv1.3 0.17 с
kws4.offshor.co.uk OK TLSv1.3 0.19 с
```

Вывод: в текущей сети CF-домены **сейчас не блокируются**, поэтому TLS-тест
проходит в любом случае. Это point-in-time проверка, а не доказательство
работы zapret. Для воспроизведения исходной проблемы нужен провайдер/сеть,
где SNI действительно режется; сравнение «с zapret / без zapret» обязательно.

---

## 9. CF-домены: источник и декодер

- Источник доступен:
  `https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt`
  — 20 закодированных строк.
- Декодер проверен на всех векторах SPEC 10.2:

| Вход | Выход |
|---|---|
| `virkgj.com` | `pclead.co.uk` |
| `vmmzovy.com` | `offshor.co.uk` |
| `mkuosckvso.com` | `cakeisalie.co.uk` |
| `zaewayzmplad.com` | `noskomnadzor.co.uk` |
| `khgrre.com` | `ebally.co.uk` |
| `nothingthere` | `nothingthere` (без изменений) |

- Живой список от 20.11.2026 декодируется **ровно в 20 доменов fallback**
  (сравнение множеств: `True`, различий нет).
- `scripts/decode-cf-domains.py` раскодирует upstream-файл; отдельный
  статический `cf-domains.txt` больше не нужен — список берётся из
  `.github/cfproxy-domains.txt` и декодируется тем же алгоритмом.

---

## 10. Инструменты и тесты

| Инструмент | Наличие |
|---|---|
| `nft` | `/usr/sbin/nft` |
| `curl` | `/usr/bin/curl` |
| `ss` | `/usr/bin/ss` |
| `systemctl` | `/usr/bin/systemctl` |
| `python3` | 3.12.3 |
| `pip` / `ensurepip` | **отсутствуют** |
| `pytest` | **отсутствует** (dpkg пуст) |
| `venv` | модуль есть, но pip в venv не создаётся (нет ensurepip) |

Поэтому unit-тесты написаны на stdlib `unittest` и совместимы с pytest
(DECISIONS D5). Запуск: `make test`.

---

## 11. Baseline-бэкап (до любых правок)

Создан снимок:

```
backups/zapret-baseline-20261120-043603/
├── config
├── zapret-hosts-user.txt
├── zapret-hosts-user-exclude.txt
├── zapret-hosts-rut.txt
└── MANIFEST.txt   (дата, хост, sha256)
```

SHA256:

```
d66eb087…  config
3bf3cc95…  zapret-hosts-user.txt
2c458a13…  zapret-hosts-user-exclude.txt
83b92d93…  zapret-hosts-rut.txt
```

Для повторного снимка: `scripts/backup-zapret.sh` (только чтение, root не нужен).

---

## 12. Что подтвердить вручную под root

```bash
# 1. Живые правила (агенту недоступны)
sudo nft list ruleset | sed -n '/table inet zapret2/,/^}/p'

# 2. Рестарт и проверка процесса
sudo systemctl restart zapret2
systemctl is-active zapret2
pgrep -a nfqws2

# 3. Хвост журнала при ошибке
sudo journalctl -u zapret2 -n 50 --no-pager

# 4. Чек-лист перед первой правкой hostlist
scripts/backup-zapret.sh
# → затем merge через linfix-helper domains.merge (M1)
```

Откат при неудачном рестарте: вернуть файл из
`backups/zapret-baseline-*/zapret-hosts-user.txt`, затем
`sudo systemctl restart zapret2` и проверить `pgrep -a nfqws2`.

---

## 13. Следствия для реализации

1. **Детект:** поддерживать `zapret2` и `zapret`; имя юнита, бинарника и
   каталога брать из фактов, а не хардкодить.
2. **PID:** `MainPID` у `zapret2.service` всегда `0`; искать `nfqws2` в `/proc`.
3. **Hostlist:** выбирать первый `--hostlist` без `.gz`, предпочитая `*user*`.
4. **Merge:** сохранять CRLF, не удалять старые домены, не трогать файл при
   отсутствии изменений.
5. **nft:** проверять таблицу `inet zapret2`; без root — WARN, не FAIL.
6. **VPN:** WARN в doctor; TLS-тест трактовать осторожно.
7. **Таймер списков:** hostlist не перезаписывает, но state.json всё равно нужен.
8. **Права:** все правки — только через `linfix-helper` (root), с бэкапом и
   откатом.
