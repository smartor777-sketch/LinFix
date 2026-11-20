#!/bin/sh
# Сборка .deb-пакета LinFix: dist/linfix_<version>_all.deb
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$ROOT/src'); import linfix; print(linfix.__version__)")
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/linfix-deb.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT

PYLIB=/usr/lib/linfix
PKG_ROOT="$STAGE/$PYLIB"
mkdir -p "$PKG_ROOT" \
    "$STAGE/DEBIAN" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/libexec" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/polkit-1/actions" \
    "$STAGE/usr/share/linfix" \
    "$STAGE/usr/lib/systemd/user"

# Python-пакет (без кэша байткода).
cp -a "$ROOT/src/linfix" "$PKG_ROOT/"
find "$PKG_ROOT/linfix" -name __pycache__ -type d -prune -exec rm -rf {} +

# Данные приложения (иконки, локаль, стратегии).
cp -a "$ROOT/src/linfix/data/." "$STAGE/usr/share/linfix/"

# Исполняемые обёртки.
install -m 755 "$ROOT/packaging/debian/linfix.launcher" "$STAGE/usr/bin/linfix"
install -m 755 "$ROOT/packaging/debian/linfix-gui.launcher" "$STAGE/usr/bin/linfix-gui"
install -m 755 "$ROOT/packaging/debian/linfix-helper.launcher" "$STAGE/usr/libexec/linfix-helper"

# .desktop, иконка, polkit.
install -m 644 "$ROOT/packaging/debian/linfix.desktop" "$STAGE/usr/share/applications/linfix.desktop"
install -m 644 "$ROOT/src/linfix/data/icons/linfix.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/linfix.svg"
install -m 644 "$ROOT/data/polkit/org.linfix.policy" "$STAGE/usr/share/polkit-1/actions/org.linfix.policy"

# systemd user-юниты (устанавливаются в систему, активируются пользователем).
for unit in tgwsproxy.service linfix-guard.service linfix-guard.timer; do
    if [ -f "$ROOT/data/systemd/user/$unit" ]; then
        install -m 644 "$ROOT/data/systemd/user/$unit" "$STAGE/usr/lib/systemd/user/$unit"
    fi
done

# control
sed "s/@VERSION@/$VERSION/" "$ROOT/packaging/debian/control" > "$STAGE/DEBIAN/control"
install -m 755 "$ROOT/packaging/debian/postinst" "$STAGE/DEBIAN/postinst"
install -m 755 "$ROOT/packaging/debian/prerm" "$STAGE/DEBIAN/prerm"

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/linfix_${VERSION}_all.deb"
echo "готово: $ROOT/dist/linfix_${VERSION}_all.deb"
