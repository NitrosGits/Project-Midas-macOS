#!/bin/bash
# package_macos.sh - wrap the native macOS build in a double-clickable
# ProjectMidas.app with SDL bundled, ad-hoc signed, and zipped.
#
#   ./scripts/package_macos.sh [build_dir] [out_dir]     (defaults: build_mac dist)
#
# Inside the app the game keeps its files in
# ~/Library/Application Support/Project Midas/ (port/src/main.c), so the ROM
# goes in that folder's Data/ subfolder.
set -euo pipefail

BUILD=${1:-build_mac}
OUT=${2:-dist}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP="$OUT/ProjectMidas.app"
EXE="$BUILD/ProjectMidas"

[ -x "$EXE" ] || { echo "no $EXE - build first (ninja -C $BUILD)"; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/Data" "$APP/Contents/Frameworks"
cp "$EXE" "$APP/Contents/MacOS/ProjectMidas"
cp "$ROOT"/tools/gamecontrollerdb.txt "$ROOT"/tools/gamecontrollerdefault.txt \
   "$ROOT"/tools/midas_ini_help.txt "$APP/Contents/Resources/"
cp "$ROOT"/tools/shaders_base.txt "$APP/Contents/Resources/Data/"

# SDL: sdl2-compat (what the game links) loads SDL3 at run time from
# @loader_path/libSDL3.dylib, so both go in Frameworks.
SDL2=$(otool -L "$EXE" | awk '/libSDL2/{print $1; exit}')
[ -n "$SDL2" ] || { echo "the executable doesn't link SDL2?"; exit 1; }
SDL2_REAL=$(realpath "$SDL2")
SDL3_REAL=$(realpath "$(dirname "$SDL2_REAL")/../../sdl3/lib/libSDL3.0.dylib" 2>/dev/null \
            || realpath "$(brew --prefix sdl3)/lib/libSDL3.0.dylib")
cp "$SDL2_REAL" "$APP/Contents/Frameworks/libSDL2-2.0.0.dylib"
cp "$SDL3_REAL" "$APP/Contents/Frameworks/libSDL3.dylib"
chmod u+w "$APP"/Contents/Frameworks/*.dylib
install_name_tool -id @rpath/libSDL2-2.0.0.dylib "$APP/Contents/Frameworks/libSDL2-2.0.0.dylib"
install_name_tool -id @rpath/libSDL3.dylib "$APP/Contents/Frameworks/libSDL3.dylib"
install_name_tool -change "$SDL2" @rpath/libSDL2-2.0.0.dylib "$APP/Contents/MacOS/ProjectMidas"
install_name_tool -add_rpath @executable_path/../Frameworks "$APP/Contents/MacOS/ProjectMidas"

# Anything else from Homebrew left behind would only work on this machine.
if otool -L "$APP/Contents/MacOS/ProjectMidas" "$APP"/Contents/Frameworks/*.dylib | grep -q "/opt/homebrew\|/usr/local/"; then
    otool -L "$APP/Contents/MacOS/ProjectMidas" "$APP"/Contents/Frameworks/*.dylib | grep "/opt/homebrew\|/usr/local/"
    echo "unbundled Homebrew libraries remain"; exit 1
fi

VERSION=$(git -C "$ROOT" describe --tags --always 2>/dev/null || echo dev)
BUILDNUM=$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>ProjectMidas</string>
    <key>CFBundleIdentifier</key><string>io.github.nitrosgits.projectmidas</string>
    <key>CFBundleName</key><string>Project Midas</string>
    <key>CFBundleDisplayName</key><string>Project Midas</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleVersion</key><string>${BUILDNUM}</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.games</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# Ad-hoc signature: Apple Silicon refuses to run unsigned code, and
# install_name_tool invalidated the copied libraries' signatures.
codesign --force --sign - "$APP"/Contents/Frameworks/*.dylib
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"

ditto -c -k --keepParent "$APP" "$OUT/ProjectMidas-macos-arm64.zip"
echo "built $APP and $OUT/ProjectMidas-macos-arm64.zip"
echo "ROM goes in: ~/Library/Application Support/Project Midas/Data/"
