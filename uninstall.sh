#!/bin/bash
# 卸载听记：停掉录音器、后台、网页三个 launchd 服务，删掉 /Applications/听记.app。
# 录音、逐句原文和密钥都留着，在 ~/Library/Application Support/Tingji，不要了自己删。
set -uo pipefail
for kind in recorder worker web; do
  label="com.biancuihuo.tingji.$kind"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
pkill -x Tingji 2>/dev/null
rm -rf "/Applications/听记.app"
echo "已卸载。数据还在：$HOME/Library/Application Support/Tingji"
