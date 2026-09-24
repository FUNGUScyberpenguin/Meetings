#!/usr/bin/env bash
# Builds "Meeting Recorder.app" and a .dmg on a Mac (macOS 13 or later, Xcode command
# line tools installed: xcode-select --install).
#
#   ./scripts/build_macos.sh            full build: helper, venv, app, dmg
#   ./scripts/build_macos.sh helper     just the Swift helper (enough to run from source)
#
# Output: dist/Meeting Recorder.app and dist/MeetingRecorder-mac-<arch>-<version>.dmg
set -euo pipefail
cd "$(dirname "$0")/.."

arch="$(uname -m)"
mkdir -p packaging/macos/build
echo "Compiling the ScreenCaptureKit helper for $arch"
swiftc -O -parse-as-library \
  -target "${arch}-apple-macos13.0" \
  packaging/macos/AudioHelper.swift \
  -o packaging/macos/build/mr-audio-helper
packaging/macos/build/mr-audio-helper version >/dev/null

if [[ "${1:-}" == "helper" ]]; then
  echo "Helper built. Run from source with: .venv/bin/python -m meeting_recorder"
  exit 0
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]' 'pyinstaller>=6.10'
.venv/bin/python -m pytest -q
.venv/bin/pyinstaller packaging/MeetingRecorder.spec --noconfirm --clean

app="dist/Meeting Recorder.app"
# Ad-hoc signature so Apple Silicon will run it. Replace "-" with a Developer ID
# identity (and notarize) to ship it without Gatekeeper warnings.
codesign --force --deep --sign - "$app"

"$app/Contents/MacOS/MeetingRecorder" --selftest dist/selftest.json || {
  cat dist/selftest.json; echo "Self-test failed"; exit 1; }
cat dist/selftest.json

version="$(.venv/bin/python -c 'import meeting_recorder; print(meeting_recorder.__version__)')"
dmg="dist/MeetingRecorder-mac-${arch}-${version}.dmg"
staging="$(mktemp -d)"
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
hdiutil create -volname "Meeting Recorder" -srcfolder "$staging" -ov -format UDZO "$dmg"
rm -rf "$staging"
echo "Built $app and $dmg"
