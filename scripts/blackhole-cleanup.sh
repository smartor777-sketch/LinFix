#!/bin/sh
# Убрать маршруты-заглушки IPv6, которые оставляют VPN-клиенты (qeli и др.).
#
# Зачем: многие клиенты включают IPv6 kill-switch — ставят blackhole-маршруты,
# чтобы трафик не утёк мимо туннеля. Если клиент закрыть не штатно (например,
# закрыть терминал), процесс не успевает прибрать за собой, и маршруты
# остаются. В результате весь IPv6 молча уходит в никуда: браузер пробует
# AAAA-запись, ждёт таймаут и только потом переходит на IPv4 — сайты
# «не открываются», хотя curl -4 работает.
#
# Скрипт снимает только маршруты-заглушки этого вида и только если
# VPN-клиент уже не запущен. Если клиент работает — маршруты нужны ему,
# и скрипт ничего не трогает.
#
# Использование (нужен root):
#   sudo scripts/blackhole-cleanup.sh
#
# Выход: 0 — чисто или почищено, 1 — VPN ещё работает, 2 — нет root.

set -eu

# Маршруты, которые держат VPN-клиенты при включённом kill-switch.
ROUTES="::/1 8000::/1 2000::/4 3000::/4 fc00::/7"

if [ "$(id -u)" -ne 0 ]; then
    echo "нужен root: sudo $0" >&2
    exit 2
fi

# Любой из известных клиентов: если он жив, маршруты — часть его работы.
for client in qeli amnezia-linux wg-quick openvpn; do
    if pgrep -x "$client" >/dev/null 2>&1; then
        echo "VPN ($client) ещё запущен — маршруты нужны ему, ничего не меняю"
        exit 1
    fi
done

# Туннельные интерфейсы — тоже признак активного VPN.
if ip -brief link 2>/dev/null | awk '{print $1}' | grep -qE '^(tun|vpn|wg)[0-9]+$'; then
    echo "активен туннельный интерфейс — маршруты нужны VPN, ничего не меняю"
    exit 1
fi

removed=0
for route in $ROUTES; do
    if ip -6 route del blackhole "$route" 2>/dev/null; then
        echo "удалён blackhole $route"
        removed=$((removed + 1))
    fi
done

left=$(ip -6 route show 2>/dev/null | grep -c '^blackhole' || true)
if [ "$removed" -eq 0 ]; then
    echo "маршрутов-заглушек нет — чисто"
else
    echo "удалено маршрутов: $removed"
fi

if [ "$left" -eq 0 ]; then
    echo "IPv6-заглушек не осталось: браузер сразу уйдёт на IPv4"
    exit 0
fi

echo "внимание: остались blackhole-маршруты ($left) — показываю:" >&2
ip -6 route show | grep '^blackhole' >&2
exit 1
