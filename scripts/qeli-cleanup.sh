#!/bin/sh
# Убрать маршруты-заглушки IPv6, которые оставляет qeli после аварийного выхода.
#
# Зачем: клиент qeli включает IPv6 kill-switch — ставит blackhole-маршруты,
# чтобы трафик не утёк мимо туннеля. Если закрыть терминал, процесс не
# успевает прибрать за собой, и маршруты остаются. В результате весь IPv6
# молча уходит в никуда: браузер пробует AAAA, ждёт таймаут и только потом
# переходит на IPv4 — сайты «не открываются», хотя curl -4 работает.
#
# Скрипт снимает только те маршруты, которые ставит qeli, и только если
# клиент уже не запущен. Если qeli ещё работает — ничего не трогает.
#
# Использование (нужен root):
#   sudo scripts/qeli-cleanup.sh
#
# Выход: 0 — чисто или почищено, 1 — qeli всё ещё работает, 2 — нет root.

set -eu

# Маршруты, которые qeli держит при включённом kill-switch.
ROUTES="::/1 8000::/1 2000::/4 3000::/4 fc00::/7"

if [ "$(id -u)" -ne 0 ]; then
    echo "нужен root: sudo $0" >&2
    exit 2
fi

if pgrep -x qeli >/dev/null 2>&1; then
    echo "qeli ещё запущен — маршруты нужны ему, ничего не меняю"
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
    echo "маршрутов qeli нет — чисто"
else
    echo "удалено маршрутов: $removed"
fi

# Заодно проверяем, что IPv6-попытка падает быстро, а не висит.
if [ "$left" -eq 0 ]; then
    echo "IPv6-заглушек не осталось: браузер сразу уйдёт на IPv4"
    exit 0
fi

echo "внимание: остались blackhole-маршруты ($left) — показываю:" >&2
ip -6 route show | grep '^blackhole' >&2
exit 1
