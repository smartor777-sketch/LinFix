#!/bin/sh
# Интеграционные проверки NetFix (M1+).
# Требуют установленный zapret, root и рабочую сеть.
set -eu

echo "M0: интеграционные проверки ещё не автоматизированы."
echo "Ручной чек-лист: docs/INVESTIGATION.md, раздел 12."
echo
echo "Планируемые шаги M1:"
echo "  1. scripts/backup-zapret.sh"
echo "  2. netfix domains list"
echo "  3. sudo netfix-helper domains.merge --hostlist <path> --domain ..."
echo "  4. sudo systemctl restart zapret2 && pgrep -a nfqws2"
echo "  5. netfix domains test --all"
