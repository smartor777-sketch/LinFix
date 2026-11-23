#!/bin/sh
# Привязать TLS-профиль к своему hostlist (--hostlist=) и дать рабочую стратегию.
#
# Зачем: если путь к списку указан «голым» (без --hostlist=), правило
# применяется ко ВСЕМУ TLS-трафику, а не к своему списку. Плюс стратегия
# tcpseg не лечит SNI-блокировку: она режет поток, но не подменяет
# ClientHello, поэтому DPI всё равно видит имя домена.
#
# Скрипт заменяет такой профиль на:
#   --filter-tcp=443 --filter-l7=tls --hostlist=<список> \
#     --payload=tls_client_hello \
#     --lua-desync=fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000 \
#     --lua-desync=multidisorder:pos=1,midsld --new
#
# Список остаётся отдельным по назначению: телеграмные домены в него не
# попадают. Чтобы «спрятать» новый сайт за CF, достаточно дописать домен
# в этот файл.
#
# Использование (нужен root):
#   sudo scripts/scope-tls-profile.sh [список] [каталог-zapret]
#
# По умолчанию список — ipset/zapret-hosts-rut.txt, каталог — /opt/zapret2
# (затем /opt/zapret).
set -eu

LIST_ARG="${1:-}"
SRC="${2:-}"
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
LIST="${LIST_ARG:-$SRC/ipset/zapret-hosts-rut.txt}"
SERVICE=$(systemctl list-unit-files 2>/dev/null | grep -q '^zapret2\.service' && echo zapret2 || echo zapret)

if [ ! -f "$CONFIG" ]; then
    echo "конфиг не найден: $CONFIG" >&2
    exit 1
fi
if [ ! -f "$LIST" ]; then
    echo "список не найден: $LIST" >&2
    echo "создайте файл с доменами (по одному в строке) и повторите" >&2
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "нужен root: sudo $0" >&2
    exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$CONFIG.bak-linfix-scope-$TS"
cp -a "$CONFIG" "$BACKUP"
echo "бэкап: $BACKUP"

python3 - "$CONFIG" "$LIST" <<'PY'
import sys
from pathlib import Path

config = Path(sys.argv[1])
target = sys.argv[2]
text = config.read_text(encoding="utf-8")

start = text.index('NFQWS2_OPT="\n') + len('NFQWS2_OPT="\n')
end = text.index('\n"\n', start)
lines = [line for line in text[start:end].splitlines() if line.strip()]

profile = (
    "--filter-tcp=443 --filter-l7=tls "
    f"--hostlist={target} --payload=tls_client_hello "
    "--lua-desync=fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000 "
    "--lua-desync=multidisorder:pos=1,midsld --new"
)

changed = False
result: list[str] = []
for line in lines:
    # «Голый» путь к этому списку — переписываем на --hostlist и стратегию.
    tokens = line.split()
    bare = target in tokens
    if bare:
        result.append(profile)
        changed = True
        continue
    # Уже привязан, но со старой стратегией — обновляем стратегию.
    if f"--hostlist={target}" in line:
        result.append(profile)
        changed = True
        continue
    result.append(line)

if not changed:
    print("профиль для этого списка не найден — добавляю первым")
    result.insert(0, profile)

config.write_text(text[:start] + "\n".join(result) + text[end:], encoding="utf-8")
print("профиль обновлён")
PY

systemctl restart "$SERVICE"

i=0
while [ "$i" -lt 20 ]; do
    if [ "$(systemctl is-active "$SERVICE")" = "active" ]; then
        break
    fi
    i=$((i + 1))
    sleep 0.5
done

if [ "$(systemctl is-active "$SERVICE")" != "active" ]; then
    echo "$SERVICE не поднялся — откат конфига" >&2
    cp -a "$BACKUP" "$CONFIG"
    systemctl restart "$SERVICE"
    exit 1
fi

if ! pgrep -af nfqws2 | grep -q -- "--hostlist=$LIST"; then
    echo "новый профиль не виден в nfqws2 — откат конфига" >&2
    cp -a "$BACKUP" "$CONFIG"
    systemctl restart "$SERVICE"
    exit 1
fi

echo "$SERVICE: active, профиль привязан к $LIST"

echo "проверка TLS по доменам из списка:"
python3 - "$LIST" <<'PY'
import socket
import ssl
import sys
from pathlib import Path

domains = [
    line.strip()
    for line in Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").splitlines()
    if line.strip() and not line.startswith("#")
]
for domain in domains[:5]:
    try:
        with socket.create_connection((domain, 443), timeout=6) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=domain) as tls:
                print(f"  {domain}: OK ({tls.version()})")
    except Exception as exc:  # noqa: BLE001
        print(f"  {domain}: FAIL ({type(exc).__name__})")
PY
