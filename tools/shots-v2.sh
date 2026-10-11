#!/bin/bash
# 正式站实拍：桌面 1440 + 手机 390，五个视图 + 衣柜
# 用法：bash shots-v2.sh [base-url]
# 无头 Chrome 的窗口宽度有下限（约 540px），设 390 会把图裁歪；这里按 560 拍，真 390 的排版另用 iframe 探针核过。
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
[ -x "$CHROME" ] || CHROME="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
BASE="${1:-http://127.0.0.1:8799}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/shots-v2"
# Chrome 不看 bash 的当前目录，--screenshot 必须是它自己的绝对路径
WINOUT="$(cygpath -w "$OUT")"
mkdir -p "$OUT/desktop" "$OUT/mobile"
URLS=(dashboard cards graph digest read)
for v in "${URLS[@]}"; do
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --force-prefers-reduced-motion --virtual-time-budget=9000 \
    --window-size=1440,980 --screenshot="$WINOUT\\desktop\\$v.png" \
    "$BASE/?shot=1#/$v" >/dev/null 2>&1
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --force-prefers-reduced-motion --virtual-time-budget=9000 \
    --window-size=560,900 --screenshot="$WINOUT\\mobile\\$v.png" \
    "$BASE/?shot=1#/$v" >/dev/null 2>&1
done
# 阅读页要带一张真实卡片
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --force-prefers-reduced-motion --virtual-time-budget=9000 \
  --window-size=1440,980 --screenshot="$WINOUT\\desktop\\read-card.png" \
  "$BASE/?shot=1#/read/wiki%2Fconcepts%2FAI%20%E8%AF%A5%E6%8C%81%E7%BB%AD%E7%BB%B4%E6%8A%A4%E7%BB%93%E6%9E%84%EF%BC%8C%E4%BA%BA%E5%8F%AA%E8%B4%9F%E8%B4%A3%E6%89%94%E5%8E%9F%E6%96%99%E5%92%8C%E6%8F%90%E9%97%AE.md" >/dev/null 2>&1
# 衣柜
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --force-prefers-reduced-motion --virtual-time-budget=9000 \
  --window-size=1440,980 --screenshot="$WINOUT\\desktop\\wardrobe.png" \
  "$BASE/?shot=1&wardrobe=1#/dashboard" >/dev/null 2>&1
ls -la "$OUT/desktop" "$OUT/mobile"
python - "$OUT" <<'PY'
import sys, os
from PIL import Image
out = sys.argv[1]
for kind, cols in (("desktop", 2), ("mobile", 3)):
    d = os.path.join(out, kind)
    fs = [f for f in sorted(os.listdir(d)) if f.endswith(".png")]
    if not fs:
        continue
    ims = [Image.open(os.path.join(d, f)) for f in fs]
    print(kind, [(f, im.size) for f, im in zip(fs, ims)])
    w = max(i.width for i in ims)
    ims = [i.resize((w, int(i.height * w / i.width))) if i.width != w else i for i in ims]
    rows = (len(ims) + cols - 1) // cols
    h = max(i.height for i in ims)
    sheet = Image.new("RGB", (w * cols + 10 * (cols + 1), h * rows + 10 * (rows + 1)), (238, 236, 242))
    for k, im in enumerate(ims):
        r, c = divmod(k, cols)
        sheet.paste(im, (10 + c * (w + 10), 10 + r * (h + 10)))
    sheet.save(os.path.join(out, "grid-%s.jpg" % kind), quality=82)
PY
