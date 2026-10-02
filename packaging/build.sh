#!/bin/sh
# Build packaging/dist/RylanFlow.app (menu-bar only). Run from the repo root.
#
# Signed with the stable "RylanFlow Local Signing" identity when available (see
# make_signing_cert.sh), so macOS Accessibility/Input Monitoring grants survive a rebuild.
# Falls back to ad-hoc signing, which resets those permissions on every build, if that
# identity hasn't been created yet.
set -e
rm -rf packaging/build packaging/dist
uv run pyinstaller --noconfirm --windowed --name RylanFlow \
  --osx-bundle-identifier com.rylananderson.rylanflow \
  --collect-all mlx --collect-all mlx_whisper --collect-all sounddevice \
  --hidden-import rumps --hidden-import pynput.keyboard._darwin --hidden-import WebKit \
  --add-data "../src/rylanflow/dashboard/static:rylanflow/dashboard/static" \
  --distpath packaging/dist --workpath packaging/build --specpath packaging \
  packaging/launcher.py
PLIST=packaging/dist/RylanFlow.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'RylanFlow records your voice to transcribe it on this Mac.'" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.2.0" "$PLIST"

SIGN_IDENTITY="-"  # ad-hoc fallback
if security find-certificate -c "RylanFlow Local Signing" >/dev/null 2>&1; then
    SIGN_IDENTITY="RylanFlow Local Signing"
else
    echo "Note: 'RylanFlow Local Signing' identity not found; falling back to ad-hoc signing."
    echo "Run packaging/make_signing_cert.sh once to make permissions survive rebuilds."
fi
codesign --force --deep --sign "$SIGN_IDENTITY" packaging/dist/RylanFlow.app
echo "Built packaging/dist/RylanFlow.app (signed with: $SIGN_IDENTITY)"
