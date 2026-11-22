#!/bin/sh
# Применить рабочий профиль zapret для CF-доменов TgWsProxy.
#
# Зачем: профиль tcpseg ломает передачу больших тел через HTTP/2, из-за чего
# медиа в Telegram не грузится (H2-канал закрывается, не получив заголовки).
# Рабочий профиль — fake + multidisorder: H2 принимает тела 256 КБ и больше.
#
# Использование (нужен root):
#   sudo scripts/apply-cf-profile.sh [путь-к-zapret]
#
# Скрипт:
#  1. находит каталог zapret (по умолчанию /opt/zapret2, затем /opt/zapret);
#  2. делает бэкап конфига;
#  3. создаёт hostlist с CF-хостами kws1..kws5, kws203 × домены;
#  4. добавляет первым профиль fake + multidisorder для этого hostlist;
#  5. перезапускает zapret и откатывается, если служба не поднялась.
set -eu

SRC="${1:-}"
if [ -z "$SRC" ]; then
    for candidate in /opt/zapret2 /opt/zapret; do
        if [ -d "$candidate" ]; then
            SRC="$candidate"
            break
        fi
    done
fi

if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
    echo "zapret не найден (нет /opt/zapret2 или /opt/zapret)" >&2
    exit 1
fi

CONFIG="$SRC/config"
HOSTLIST="$SRC/ipset/linfix-cf-hosts.txt"
SERVICE=$(systemctl list-unit-files 2>/dev/null | grep -q '^zapret2\.service' && echo zapret2 || echo zapret)

if [ ! -f "$CONFIG" ]; then
    echo "конфиг не найден: $CONFIG" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "нужен root: sudo $0" >&2
    exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$CONFIG.bak-linfix-cf-$TS"
cp -a "$CONFIG" "$BACKUP"
echo "бэкап: $BACKUP"

# CF-хосты: kwsN и kwsN-1 для DC 1..5 и CDN-канала 203.
{
    for dc in 1 2 3 4 5 203; do
        for domain in \
            pclead.co.uk offshor.co.uk cakeisalie.co.uk noskomnadzor.co.uk \
            lovetrue.co.uk sorokdva.co.uk pyatdesyatdva.co.uk kartoshka.co.uk \
            sorokodin.co.uk pyatdesyatodin.co.uk notelega.co.uk ebally.co.uk \
            nebally.co.uk havegreatday.co.uk pomogite.co.uk fixtelega.co.uk \
            sadnews.co.uk onedaychamp.co.uk stopblocking.co.uk nothingthere.co.uk
        do
            echo "kws$dc.$domain"
        done
    done
} > "$HOSTLIST"
chmod 644 "$HOSTLIST"
echo "hostlist: $HOSTLIST ($(wc -l < "$HOSTLIST") хостов)"

python3 - "$CONFIG" "$HOSTLIST" <<'PY'
import sys
from pathlib import Path

config = Path(sys.argv[1])
hostlist = sys.argv[2]
text = config.read_text(encoding="utf-8")

start_marker = 'NFQWS2_OPT="\n'
start = text.index(start_marker) + len(start_marker)
end = text.index('\n"\n', start)
body = text[start:end]
lines = [line for line in body.splitlines() if line.strip()]

if any("linfix-cf-hosts.txt" in line for line in lines):
    print("профиль уже есть, повторно не добавляю")
else:
    profile = (
        "--filter-tcp=443 --filter-l7=tls "
        f"--hostlist={hostlist} --payload=tls_client_hello "
        "--lua-desync=fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000 "
        "--lua-desync=multidisorder:pos=1,midsld --new"
    )
    lines.insert(0, profile)
    config.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
    print("профиль добавлен")
PY

systemctl restart "$SERVICE"

for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ "$(systemctl is-active "$SERVICE")" = "active" ]; then
        echo "$SERVICE: active"
        echo "проверка H2 (ожидаем 404 за <1 с):"
        python3 - <<'PY' || true
import os
import httpx

try:
    with httpx.Client(http2=True, timeout=10) as client:
        response = client.post(
            "https://kws2.pclead.co.uk/api",
            content=os.urandom(262144),
            headers={"content-type": "application/octet-stream", "accept-encoding": "identity"},
        )
    print("  256 КБ -> HTTP", response.status_code)
except Exception as exc:
    print("  256 КБ ->", type(exc).__name__)
PY
        exit 0
    fi
    sleep 1
done

echo "$SERVICE не поднялся — откат конфига" >&2
cp -a "$BACKUP" "$CONFIG"
systemctl restart "$SERVICE"
exit 1
