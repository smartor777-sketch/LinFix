# Решение: медиа Telegram через CF/H2 (22.11.2026)

## Симптом

- Текст, реакции и обновления в Telegram работают.
- Медиа (фото, видео, файлы) не загружается ни в одном канале.
- TgWsProxy показывает `ws=0`, медиа уходит в fallback, сессии живут
  ~15 секунд и получают 0.5–13 КБ.

## Корневая причина

Профиль zapret `tcpseg:pos=0,1:ip_id=rnd:repeats=1`, который применялся
ко всем TLS-соединениям, **ломает передачу больших тел через HTTP/2**.

`tcpseg` режет TCP-сегменты в потоке. На служебных пакетах это незаметно,
но на телах больше ~16–24 КБ вносит рассинхрон: H2-канал отправляет запрос,
не получает заголовки ответа, залипает на 8 секунд и закрывается.

## Как нашли

1. Снимок рабочей Windows-конфигурации
   (`docs/TELEGRAM-CONNECTION-PARAMS.md`) показал: `h2=true`, `ws=0`,
   скачано **425 МБ**. Значит CF/H2 в принципе работает.
2. Остановка zapret целиком дала `ConnectTimeout` — обход нужен, но не тот.
3. Прогон 8 профилей desync с проверкой `TLS + H2 POST 16/64/256 КБ`:

| Профиль | 64 КБ | 256 КБ |
|---|---|---|
| `tcpseg:pos=0,1:ip_id=rnd:repeats=1` | WriteTimeout | WriteTimeout |
| без desync | WriteTimeout | WriteTimeout |
| `fake:blob=fake_default_tls:tcp_md5` | 404 / 0.4 с | 404 / 0.6 с |
| `fake + multidisorder:pos=1,midsld` | 404 / 0.4 с | 404 / 0.4 с |
| `fake + multisplit:pos=1` | 404 / 0.4 с | 404 / 0.5 с |
| `multidisorder` / `multisplit` / `fakedsplit` | TLS FAIL | TLS FAIL |

## Что изменено

### zapret

Добавлен отдельный профиль только для CF-доменов, остальные профили не
тронуты. В `/opt/zapret2/config`, первой строкой в `NFQWS2_OPT`:

```text
--filter-tcp=443 --filter-l7=tls --hostlist=/opt/zapret2/ipset/linfix-cf-hosts.txt \
  --payload=tls_client_hello \
  --lua-desync=fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000 \
  --lua-desync=multidisorder:pos=1,midsld --new
```

Список `/opt/zapret2/ipset/linfix-cf-hosts.txt` — 120 хостов:
`kws1..kws5` и `kws203` × 20 доменов из fallback-списка
(`linfix.cfdomains.FALLBACK_DOMAINS`, он же — раскодированный upstream).

Бэкап: `/opt/zapret2/config.bak-linfix-20261122-0428`.

### TgWsProxy

- `h2=true` — H2-медиа снова включён.
- `cfproxy_user_domain` — все 20 живых `.co.uk`-доменов, а не 3.
- `cfproxy_user_domain_enabled=true`.
- `verbose=false`.

### Сеть

- `wlo1` MTU 1492 (PMTU до CF = 1492, с 1500 была «чёрная дыра»).
- `net.ipv4.tcp_mtu_probing=1`.
- Wi-Fi переведён на 5 ГГц (канал 48): джиттер упал с 23 мс до 3.6 мс.

## Результат

За 50 секунд после включения:

```text
channel=1 down=6.06MB   channel=2 down=9.21MB
channel=4 down=3.28MB   channel=5 down=1.18MB
down=18.9MB  h2_err=0
```

## Как проверить быстро

```bash
# H2 принимает большие тела
python3 - <<'PY'
import os, httpx
with httpx.Client(http2=True, timeout=10) as c:
    r = c.post("https://kws2.pclead.co.uk/api", content=os.urandom(262144),
               headers={"content-type":"application/octet-stream","accept-encoding":"identity"})
    print(r.status_code)
PY

# профиль и hostlist на месте
grep -n 'linfix-cf-hosts' /opt/zapret2/config
systemctl is-active zapret2
```

## Важно на будущее

- `tcpseg` не использовать для CF-доменов: он ломает H2-медиа.
- Профиль `fake + multidisorder` совпадает с рабочим Windows-конфигом.
- Публичный GitHub-список `cfproxy-domains.txt` закодирован: `.com`-строки —
  это коды (`virkgj.com` → `pclead.co.uk`). LinFix декодирует их тем же
  алгоритмом, что и TgWsProxy, и получает те же 20 `.co.uk`-доменов.
  Прямой DNS-запрос к `.com`-коду даёт NXDOMAIN — это ожидаемо.
