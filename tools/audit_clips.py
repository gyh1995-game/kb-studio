# -*- coding: utf-8 -*-
"""巡检原料：卡列表重复、处理日志混进正文、原文/消化稿顺序。只读不改。"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import server  # 库路径跟着 server.VAULT 走，别写死任何人的机器路径
VAULT = Path(sys.argv[1] if len(sys.argv) > 1 else server.VAULT)
LOG_PAT = re.compile(
    r"旧稿已备份|没覆盖掉|最近一次重生成|没走 DeepSeek|由 agent 自己读|由当前会话的模型|"
    r"maxOcrImages|之前被当成图文|只识了封面|修好取址后重跑|第二次重跑|这一节带出来的|"
    r"光听音轨|本次长成的卡|这张原料长成的卡|faster-whisper|FunASR|本地转写|截断")

for p in sorted((VAULT / "raw" / "clips").glob("*.md")):
    s = p.read_text(encoding="utf-8")
    lines = s.splitlines()
    dm = [i for i, l in enumerate(lines) if re.match(r"^#\s*消化稿", l)]
    rm = [i for i, l in enumerate(lines) if re.match(r"^#\s*原文", l)]
    heads = [i for i, l in enumerate(lines) if re.match(r"^#{2,3}\s*(本次长成的卡|这张原料长成的卡)", l)]
    print("== %s" % p.name)
    print("   原文@%s 消化稿@%s 卡列表@%s" % (rm, dm, [i + 1 for i in heads]))
    if dm:
        d0 = dm[0]
        print("   消化稿内部小节: %s" % [lines[i] for i in range(d0, len(lines))
                                     if re.match(r"^#{2,3}\s", lines[i])])
    for i, l in enumerate(lines):
        if LOG_PAT.search(l):
            layer = "原文" if rm and dm and i < dm[0] else ("消化稿" if dm and i >= dm[0] else "?")
            print("   [%s:%d] %s" % (layer, i + 1, l.strip()[:110]))
    print()
