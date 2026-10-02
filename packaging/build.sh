#!/bin/sh
# Build packaging/dist/RylanFlow.app (menu-bar only, ad-hoc signed). Run from the repo root.
set -e
rm -rf packaging/build packaging/dist
uv run pyinstaller --noconfirm --windowed --name RylanFlow \
  --osx-bundle-identifier com.rylananderson.rylanflow \
  --collect-all mlx --collect-all mlx_whisper --collect-all sounddevice \
  --hidden-import rumps --hidden-import pynput.keyboard._darwin \
  --distpath packaging/dist --workpath packaging/build --specpath packaging \
  packaging/launcher.py
PLIST=packaging/dist/RylanFlow.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'RylanFlow records your voice to transcribe it on this Mac.'" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.1.0" "$PLIST"
codesign --force --deep --sign - packaging/dist/RylanFlow.app
echo "Built packaging/dist/RylanFlow.app"
