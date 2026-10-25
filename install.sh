#!/bin/bash
# 一键安装听记：Python 环境、模型、API 密钥、录音器 App、后台处理和时间线网页
#   ./install.sh                全部装好，开机自启
#   ./install.sh --python-only  只装 Python 环境、模型和密钥，不装 App、不开后台
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
DATA="${TINGJI_HOME:-$HOME/Library/Application Support/Tingji}"
VENV="$REPO/.venv"
PY="$VENV/bin/python"
ONLY_PY=0
[ "${1:-}" = "--python-only" ] && ONLY_PY=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

say "1/6 检查环境"
[ "$(uname)" = "Darwin" ] || die "听记只支持 macOS"
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 15 ] || die "需要 macOS 15 或更高（现在是 $(sw_vers -productVersion)）"
command -v ffmpeg >/dev/null || [ -x /opt/homebrew/bin/ffmpeg ] || [ -x /usr/local/bin/ffmpeg ] || die "没有 ffmpeg，先装：brew install ffmpeg"
PYBIN="${TINGJI_PYTHON:-}"
if [ -z "$PYBIN" ]; then
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      PYBIN="$(command -v "$c")"
      break
    fi
  done
fi
[ -n "$PYBIN" ] || die "需要 Python 3.11 或更高，先装：brew install python@3.13"
[ $ONLY_PY = 1 ] || command -v swift >/dev/null || die "需要 Swift 编译器，先装：xcode-select --install"
echo "Python：$PYBIN"

say "2/6 Python 环境：$VENV"
[ -x "$PY" ] || "$PYBIN" -m venv "$VENV"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e "$REPO"

say "3/6 模型：人声检测 Silero VAD、中文声纹 3D-Speaker CAM++（来自 sherpa-onnx）"
mkdir -p "$DATA/models"
fetch() {  # 存成的文件名 下载地址
  local f="$DATA/models/$1"
  if [ -s "$f" ]; then echo "已有 $1"; return; fi
  curl -fL --retry 3 --progress-bar -o "$f.part" "$2"
  mv "$f.part" "$f"
  echo "下载好 $1"
}
fetch silero_vad.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
fetch campplus_zh.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx

say "4/6 API 密钥"
SECRETS="$DATA/secrets.json"
if [ -f "$SECRETS" ]; then
  echo "已有 $SECRETS"
else
  DS="${DASHSCOPE_API_KEY:-}"
  DK="${DEEPSEEK_API_KEY:-}"
  if [ -z "$DS" ] && [ -t 0 ]; then read -r -s -p "阿里云百炼 API Key（转写用）：" DS; echo; fi
  if [ -z "$DK" ] && [ -t 0 ]; then read -r -s -p "DeepSeek API Key（整理用）：" DK; echo; fi
  if [ -n "$DS" ] && [ -n "$DK" ]; then
    (umask 077 && DS="$DS" DK="$DK" "$PY" -c 'import json, os, sys; json.dump({"dashscope": os.environ["DS"], "deepseek": os.environ["DK"]}, open(sys.argv[1], "w"), indent=2)' "$SECRETS")
    chmod 600 "$SECRETS"
    echo "写好 ${SECRETS}（权限 600）"
  else
    echo "先跳过。之后自己建 ${SECRETS}，内容是 {\"dashscope\": \"sk-...\", \"deepseek\": \"sk-...\"}，权限设成 600"
  fi
fi

if [ $ONLY_PY = 1 ]; then
  say "Python 部分装好了"
  exit 0
fi

say "5/6 录音器 App"
"$REPO/app/build.sh" --install

say "6/6 后台处理和时间线网页"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
for kind in worker web; do
  label="com.biancuihuo.tingji.$kind"
  plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  sed -e "s#__PYTHON__#$PY#g" -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" "$REPO/launchd/$label.plist.in" > "$plist"
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "已启动 $label"
done

say "装好了"
cat <<EOF
- 菜单栏出现波形图标就是在录。第一次会弹麦克风权限，点「允许」。
- 时间线：http://127.0.0.1:7788/
- 让它认出你：对着电脑一个人说 30 秒左右的话，等一两分钟，然后
    $VENV/bin/tingji pieces              # 找到刚才那段的编号
    $VENV/bin/tingji enroll --piece 编号
    $VENV/bin/tingji relabel
- 接到 Claude Code：
    claude mcp add tingji -s user -- $VENV/bin/tingji-mcp
EOF
