#!/usr/bin/env bash
# 第三轮验收截图：桌面 1440×980 + 手机 560×900，全部打 8799 上的临时库
# 用法：BASE=http://127.0.0.1:8799 bash tools/shots-v3.sh
set -u
CH="/c/Program Files/Google/Chrome/Application/chrome.exe"
BASE="${BASE:-http://127.0.0.1:8799}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/shots-v3"
WIN="${WIN:-1440,980}"
mkdir -p "$OUT/desktop" "$OUT/mobile"

# Chrome 要求绝对 Windows 路径；--user-data-dir 各开一个，否则多个实例抢同一把 profile 锁会静默不出图
shot() {  # shot <目录> <名字> <url> <宽,高> <序号>
  "$CH" --headless=new --disable-gpu --hide-scrollbars \
    --user-data-dir="C:/Users/ADMIN/AppData/Local/Temp/ch-p$5" \
    --force-prefers-reduced-motion --virtual-time-budget=9000 \
    --window-size="$4" --screenshot="$(cygpath -w "$OUT/$1/$2.png")" "$3" 2>&1 | tail -1
}

R="raw%2Fclips%2F20261006-Qwen3.8-27B-%E6%A8%A1%E5%9E%8B%E8%A2%AB%E8%AF%84%E4%BB%B7%E4%B8%BA%E4%BB%8A%E5%B9%B4%E6%9C%80%E4%BB%A4%E4%BA%BA%E6%84%8F.md"
C="wiki%2Fconcepts%2FAI%20%E8%AF%A5%E6%8C%81%E7%BB%AD%E7%BB%B4%E6%8A%A4%E7%BB%93%E6%9E%84%EF%BC%8C%E4%BA%BA%E5%8F%AA%E8%B4%9F%E8%B4%A3%E6%89%94%E5%8E%9F%E6%96%99%E5%92%8C%E6%8F%90%E9%97%AE.md"

n=0
for v in dashboard cards graph digest; do n=$((n+1)); shot desktop "$v" "$BASE/#/$v" "$WIN" $n; done
n=$((n+1)); shot desktop read "$BASE/#/read/$R" "$WIN" $n
n=$((n+1)); shot desktop read-card "$BASE/#/read/$C" "$WIN" $n

n=0
for v in dashboard cards graph digest; do n=$((n+1)); shot mobile "$v" "$BASE/#/$v" "560,900" $n; done
n=$((n+1)); shot mobile read "$BASE/#/read/$R" "560,900" $n
n=$((n+1)); shot mobile read-card "$BASE/#/read/$C" "560,900" $n
echo "→ $OUT"
