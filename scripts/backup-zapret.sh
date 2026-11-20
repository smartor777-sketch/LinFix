#!/bin/sh
# Снимок конфига и списков zapret перед любыми правками.
# Использование: scripts/backup-zapret.sh [/opt/zapret2|/opt/zapret]
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

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TS=$(date +%Y%m%d-%H%M%S)
DST="$ROOT/backups/$(basename "$SRC")-$TS"
mkdir -p "$DST"

copy() {
    if [ -e "$1" ]; then
        cp -a "$1" "$DST/"
    fi
}

copy "$SRC/config"
copy "$SRC/config.default"
copy "$SRC/ipset/zapret-hosts-user.txt"
copy "$SRC/ipset/zapret-hosts.txt.gz"
copy "$SRC/ipset/zapret-hosts-user-exclude.txt"
copy "$SRC/ipset/zapret-hosts-rut.txt"

{
    echo "zapret backup"
    echo "date: $(date -Is)"
    echo "host: $(hostname)"
    echo "source: $SRC"
    echo "--- sha256 ---"
    for file in "$DST"/*; do
        [ -f "$file" ] && sha256sum "$file" | sed "s#$DST/##"
    done
} > "$DST/MANIFEST.txt"

echo "$DST"
