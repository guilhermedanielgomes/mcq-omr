#!/bin/sh
# Make a .deb (Ubuntu, Debian, Mint, ...) and a .tar.gz (any distribution) from dist/MCQ-OMR.
# The .deb puts the app in the applications menu and can be removed with the software centre or
# `sudo apt remove mcq-omr`; the data in ~/.local/share/mcq-omr stays.
#   sh packaging/linux/build_deb.sh <version> [dist_dir]
set -eu
VERSION="$(echo "${1:-0.0.0}" | sed 's/^v//')"
DIST="${2:-dist}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(mktemp -d)/mcq-omr_${VERSION}_amd64"
mkdir -p "$ROOT/DEBIAN" "$ROOT/opt" "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/512x512/apps"
cp -R "$DIST/MCQ-OMR" "$ROOT/opt/mcq-omr"
ln -s /opt/mcq-omr/MCQ-OMR "$ROOT/usr/bin/mcq-omr"
cp "$HERE/mcq-omr.desktop" "$ROOT/usr/share/applications/mcq-omr.desktop"
cp "$HERE/../icons/icon-512.png" "$ROOT/usr/share/icons/hicolor/512x512/apps/mcq-omr.png"
SIZE="$(du -sk "$ROOT/opt" | cut -f1)"
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: mcq-omr
Version: $VERSION
Section: education
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Depends: xdg-utils
Maintainer: MCQ-OMR <noreply@users.noreply.github.com>
Homepage: https://github.com/guilhermedanielgomes/mcq-omr
Description: Multiple-choice exams with answer sheets read from phone photos
 Every student gets a shuffled version of the exam and a bubble answer sheet. Photos of the
 filled sheets are read and marked automatically. Runs in the browser on this computer.
CTRL
dpkg-deb --build --root-owner-group "$ROOT" "$DIST/mcq-omr_${VERSION}_amd64.deb"
tar -C "$DIST" -czf "$DIST/MCQ-OMR-$VERSION-linux-x86_64.tar.gz" MCQ-OMR
ls -la "$DIST"/*.deb "$DIST"/*.tar.gz
