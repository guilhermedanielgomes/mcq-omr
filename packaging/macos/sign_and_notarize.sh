#!/bin/sh
# Sign MCQ-OMR.app with a Developer ID certificate, make the disk image, sign it, have Apple notarize
# it and staple the ticket, so macOS opens it without any warning.
#
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   APPLE_ID=you@example.com APPLE_TEAM_ID=TEAMID APPLE_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx \
#   sh packaging/macos/sign_and_notarize.sh <version> [dist_dir]
#
# APPLE_APP_PASSWORD is an app-specific password (appleid.apple.com > Sign-In and Security).
# Without APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD the app and image are signed but not notarized.
# SIGN_IDENTITY="-" signs ad hoc (only for testing this script).
set -eu
VERSION="${1:-dev}"
DIST="${2:-dist}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$DIST/MCQ-OMR.app"
ENT="$HERE/entitlements.plist"
: "${SIGN_IDENTITY:?set SIGN_IDENTITY to your Developer ID Application identity}"
TS="--timestamp"
[ "$SIGN_IDENTITY" = "-" ] && TS=""

sign() { codesign --force --options runtime $TS --entitlements "$ENT" --sign "$SIGN_IDENTITY" "$1"; }

# inside out: every compiled library and executable first, then the app itself
find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -111 \) | while read -r f; do
  if file "$f" | grep -q "Mach-O"; then sign "$f"; fi
done
# embedded frameworks (PyInstaller bundles Python.framework) are signed as bundles, deepest first
find "$APP/Contents/Frameworks" -type d -name "*.framework" 2>/dev/null | awk '{ print length, $0 }' | sort -rn | cut -d" " -f2- |
  while read -r fw; do sign "$fw"; done
sign "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

DMG="$(sh "$HERE/build_dmg.sh" "$VERSION" "$DIST" | tail -1)"
codesign --force $TS --sign "$SIGN_IDENTITY" "$DMG"

if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG" || true
else
  echo "signed, not notarized (APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD not set)"
fi
echo "$DMG"
