#!/bin/bash
# 构建「听记」.app；加 --install 装到 /Applications，并用 launchd 开机自启
# 默认临时签名（ad-hoc）：每次重装后 macOS 可能会重新问一次麦克风权限。
# 有 Apple Development 证书的话：TINGJI_SIGN_IDENTITY="Apple Development" ./build.sh --install，签名身份固定，权限就不会丢
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="听记"
APP="$ROOT/build/${APP_NAME}.app"
IDENTITY="${TINGJI_SIGN_IDENTITY:--}"
LABEL="com.biancuihuo.tingji.recorder"

swift build -c release --package-path "$ROOT"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/.build/release/Tingji" "$APP/Contents/MacOS/Tingji"
cp "$ROOT/Resources/Info.plist" "$APP/Contents/Info.plist"
# 不开 hardened runtime：开了就得另加麦克风 entitlement。钥匙串弹窗卡住时 20 秒后退回 ad-hoc
if [ "$IDENTITY" = "-" ]; then
  codesign --force --sign - "$APP"
else
  perl -e "alarm 20; exec @ARGV" codesign --force --sign "$IDENTITY" "$APP" 2>/dev/null || codesign --force --sign - "$APP"
fi
codesign -dv "$APP" 2>&1 | grep -E "Authority|Signature" | head -2
echo "已构建：$APP"

if [ "${1:-}" = "--install" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  pkill -x Tingji 2>/dev/null || true
  sleep 1
  rm -rf "/Applications/${APP_NAME}.app"
  ditto "$APP" "/Applications/${APP_NAME}.app"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  sed -e "s#__HOME__#$HOME#g" "$ROOT/../launchd/$LABEL.plist.in" > "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "已安装并启动：/Applications/${APP_NAME}.app（菜单栏里的波形图标）"
fi
