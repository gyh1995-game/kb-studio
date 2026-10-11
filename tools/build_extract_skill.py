# -*- coding: utf-8 -*-
"""生成 extract-skill/ —— 一份能单独安装、单独跑的多平台内容提取工具。

    python tools/build_extract_skill.py            # 生成 / 更新
    python tools/build_extract_skill.py --check    # 只比对，漂移了退出码 1

为什么是「生成」而不是「复制一份放着」：fetchers.py 和那几份提示词在主程序里会继续改，
手抄一份出去，两周后两边就不是同一个东西了 —— 那份独立工具会悄悄退回旧行为。
所以这里只放**从主程序现读现抽**的内容，SKILL.md / extract.py / install.py 才是手写的。
"""
import argparse
import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SKILL = HERE / "extract-skill"
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 主程序里要带走的提示词常量：名字必须和 server.py 里一致
PROMPT_NAMES = ["OCR_PROMPT", "FRAME_OCR_PROMPT", "DIGEST_SYSTEM", "DIGEST_PROMPT"]

# 从 server.py 里原样抠出常量赋值块（三引号字符串），不 import：
# import 会触发 vault 探测和 scaffold，构建脚本不该写任何人的库。
BLOCK = re.compile(r"^(%s) = \"\"\"(.*?)\"\"\"$" % "|".join(PROMPT_NAMES), re.S | re.M)


def extract_prompts():
    src = (HERE / "server.py").read_text(encoding="utf-8")
    found = {m.group(1): m.group(2) for m in BLOCK.finditer(src)}
    missing = [n for n in PROMPT_NAMES if n not in found]
    if missing:
        raise SystemExit("server.py 里找不到这些提示词常量：%s —— 改名了就去改本脚本" % missing)
    out = ['"""提示词 —— 由 tools/build_extract_skill.py 从 server.py 抽取，别手改。"""', ""]
    for n in PROMPT_NAMES:
        out.append("%s = '''%s'''" % (n, found[n].replace("'''", "\\'\\'\\'")))
        out.append("")
    return "\n".join(out)


def wanted():
    return {
        "fetchers.py": (HERE / "fetchers.py").read_text(encoding="utf-8"),
        "prompts.py": extract_prompts(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    files = wanted()
    if a.check:
        for name, text in files.items():
            dst = SKILL / name
            if not dst.is_file() or dst.read_text(encoding="utf-8") != text:
                print("漂移了：%s 与主程序不一致，跑 python tools/build_extract_skill.py" % name)
                return 1
        print("一致：extract-skill/ 与主程序同步")
        return 0
    SKILL.mkdir(exist_ok=True)
    for name, text in files.items():
        (SKILL / name).write_text(text, encoding="utf-8")
        print("  写 %s（%d 字）" % (name, len(text)))
    print("extract-skill/ 已更新。手写的部分（SKILL.md / extract.py / install.py）没动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
