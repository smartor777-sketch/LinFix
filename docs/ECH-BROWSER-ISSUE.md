# Браузер не открывает сайт, а `curl` открывает: Encrypted Client Hello

Записано 23.11.2026 по итогам разбора rutracker.org.

## Симптом

Один и тот же сайт:

| Чем проверяем | Результат |
|---|---|
| `curl` | `301`/`200` за 0.1–0.5 с |
| `openssl s_client` | 3/3 OK |
| Python `ssl` | OK |
| **Firefox** | страница не грузится |
| **Waterfox** | страница не грузится |
| Opera / Chromium | OK |

Отсюда ложный след: «провайдер блокирует rutracker, а zapret не помогает
Firefox». На самом деле блокировка обходится, но браузер всё равно не
работает — по другой причине.

## Причина: ECH

Cloudflare отдаёт в DNS специальную HTTPS-запись с параметром `ech`:

```bash
$ dig +short HTTPS rutracker.org
1 . alpn="h3,h2" ipv4hint=104.21.32.39,172.67.182.196 \
  ech=AEX+DQBBZwAgACCIB6Yvdm7z1qSBUeQfMaY5yFF/pvhNjUFo3w8V3gZ1aAAEAAEAAQASY2xvdWRmbGFyZS1lY2guY29tAAA= \
  ipv6hint=2606:4700:3035::6815:2027,2606:4700:3037::ac43:b6c4
```

`ech=` означает Encrypted Client Hello: имя сайта (SNI) шифруется
открытым ключом из этой записи, чтобы промежуточные устройства не видели,
куда идёт соединение.

**Gecko** (движок Firefox и Waterfox) эту запись читает и пытается
использовать ECH. Провайдерский DPI такое соединение не пропускает:
TCP-сессия устанавливается, но ответа на ClientHello нет.

**Chromium** (Opera, Chrome, Brave) ECH в такой конфигурации не
использует, поэтому у них всё открывается.

## Как это видно в системе

В состоянии сокета видно, что сервер не подтвердил ни один байт:

```bash
$ ss -tni | grep -A1 ':443'
```

```
# Firefox — ClientHello висит, сервер молчит
ESTAB 0 1832 192.168.3.11:55110 172.67.182.196:443
   mss:512 cwnd:1 bytes_sent:7976 bytes_retrans:6144 bytes_acked:1

# curl — нормальное соединение
ESTAB 0 0 192.168.3.11:49886 172.67.182.196:443
   mss:1388 cwnd:10 bytes_sent:799 bytes_acked:776
```

`bytes_acked:1` при `bytes_retrans` в килобайтах — признак того, что
ClientHello уходит в никуда и повторяется по таймауту.

## Доказательство

Ключевой эксперимент — на домене, который **не заблокирован** и которого
**нет в hostlist** zapret, то есть zapret его вообще не касается.

`crypto.cloudflare.com` отдаёт ECH, но доступен:

```bash
$ dig +short HTTPS crypto.cloudflare.com | grep -o 'ech=[^ ]*'
ech=AEX+DQBBZwAgACCIB6Yvdm7z1qSBUeQfMaY5yFF...
```

Результат в headless-Firefox:

| Настройка ECH | Попыток | Успешно |
|---|---|---|
| включён (по умолчанию) | 3 | **0** |
| выключен | 3 | **3** |
| `curl` (ECH не использует) | 3 | 3 |

Один и тот же домен, один и тот же zapret, меняется только ECH.
Это исключает и провайдерскую блокировку конкретного сайта, и ошибку в
профиле zapret.

Дополнительно проверено и отброшено:

- **post-quantum (Kyber/ML-KEM)** — `security.tls.enable_kyber`,
  `security.tls.enable_mlkem1024` = `false` не помогают;
- **HTTP/3 (QUIC)** — `network.http.http3.enable = false` не помогает;
- **IPv6** — `network.dns.disableIPv6 = true` не помогает;
- **размер ClientHello** — openssl с 1 и с 8 группами работает 3/3;
- **стратегия zapret** — перебраны 4 варианта профиля, все 0/2;
- **остановка zapret** — без него Firefox тоже не работает.

### ECH и IPv6-заглушки — независимые проблемы

Часто эти две причины накладываются друг на друга, поэтому важно их не
путать. Перекрёстный эксперимент на `crypto.cloudflare.com`:

| ECH | blackhole IPv6 | Результат |
|---|---|---|
| выключен | нет | OK OK OK |
| выключен | **есть** | OK OK OK |
| включён | нет | OK FAIL OK |
| включён | есть | FAIL FAIL FAIL |

Вывод: **ECH ломает браузер сам по себе**, без всякого участия IPv6.
При выключенном ECH заглушки не мешают вовсе. При включённом они
добавляют вторую, независимую причину отказа.

Разница по сути:

- **IPv6-заглушки** — про *адрес*: пакет уходит в `blackhole` и ждёт
  таймаут. Влияет на любой браузер, лечится удалением маршрутов
  (`scripts/blackhole-cleanup.sh`).
- **ECH** — про *содержимое TLS-рукопожатия*: имя сайта зашифровано, DPI
  такое соединение не пропускает. Идёт по IPv4 и всё равно висит на
  `bytes_acked:1`. Влияет только на Gecko, лечится настройкой браузера.

## Решение

Выключить ECH в браузере. Три настройки в `about:config`:

```text
network.dns.echconfig.enabled        = false
network.dns.http3_echconfig.enabled  = false
network.dns.native_https_query       = false
```

Для Waterfox то же самое — движок тот же.

Проверить, что применилось: `about:config` → поиск `echconfig` → оба
значения `false`.

## Как применить автоматически

### Firefox (обычная установка)

Профиль: `~/.mozilla/firefox/<профиль>/`. Файл `user.js` применяется при
каждом старте, поэтому настройки не теряются:

```javascript
user_pref("network.dns.echconfig.enabled", false);
user_pref("network.dns.http3_echconfig.enabled", false);
user_pref("network.dns.native_https_query", false);
```

### Waterfox из Flatpak

В этой системе Waterfox установлен как Flatpak, поэтому его файлы лежат
не в `~/.waterfox`, а внутри песочницы:

```text
~/.var/app/net.waterfox.waterfox/.waterfox/<профиль>/user.js
```

Активный профиль виден в `installs.ini`:

```bash
$ cat ~/.var/app/net.waterfox.waterfox/.waterfox/installs.ini
[1574EAFA99996BA9]
Default=2inrqayi.default-release
```

`user.js` создаётся в каталоге этого профиля. В этой системе он уже
записан в `2inrqayi.default-release/user.js`.

### Если браузер запущен

`prefs.js` править нельзя — браузер перезапишет его при выходе.
`user.js` писать безопасно и при запущенном браузере, но настройки
подхватятся только после перезапуска.

## Когда ECH не при чём

Если `dig +short HTTPS <домен>` не содержит `ech=`, причина другая —
скорее всего, обычная SNI-блокировка, и её лечит профиль zapret
(`fake + multidisorder`, см. `scope-tls-profile.sh`).

Быстрая проверка:

```bash
dig +short HTTPS rutracker.org | grep -o 'ech=[^ ]*' || echo 'ECH нет'
```
