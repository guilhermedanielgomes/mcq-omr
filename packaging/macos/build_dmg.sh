#!/bin/sh
# Wrap dist/MCQ-OMR.app in a disk image with an Applications shortcut (drag to install).
#   sh packaging/macos/build_dmg.sh <version> [dist_dir]
set -eu
VERSION="${1:-dev}"
DIST="${2:-dist}"
ARCH="$(uname -m)"
STAGE="$(mktemp -d)"
cp -R "$DIST/MCQ-OMR.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$(cd "$(dirname "$0")/../.." && pwd)/LICENSE" "$STAGE/License.txt"
OUT="$DIST/MCQ-OMR-$VERSION-macos-$ARCH.dmg"
rm -f "$OUT"
hdiutil create -volname "MCQ-OMR" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "$OUT"
