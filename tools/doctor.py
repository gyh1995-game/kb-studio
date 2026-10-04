# -*- coding: utf-8 -*-
"""能力体检：这台机器上这个开源项目到底能跑哪几件事，缺什么、怎么补，一次说清。

    python tools/doctor.py            # 只看
    python tools/doctor.py --fix-hint # 每项缺失都附具体命令

设计原则：不联网也能跑完（网络项标成「未测」而不是失败）；任何一项坏了都不影响其他项。
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

ROWS = []


def check(group, name, ok, detail="", hint="", level="core"):
    """ok: True 可用 / False 缺失 / None 未测。"""
    ROWS.append({"group": group, "name": name, "state": ok,
                 "detail": detail, "hint": hint, "level": level})


def pyver():
    v = sys.version_info
    check("运行环境", "Python", v[:2] >= (3, 9), "%d.%d.%d" % v[:3],
          "装 Python 3.10+ 并用它跑：py -3.10 -m pip install -r requirements.txt")


def imports():
    mods = [
        ("bs4", "核心", "beautifulsoup4", "网页正文清洗"),
        ("requests", "核心", "requests", "带 cookie 的取数"),
        ("pymupdf", "核心", "pymupdf", "PDF 导入"),
        ("PIL", "核心", "pillow", "图片处理"),
        ("faster_whisper", "核心", "faster-whisper", "语音转写兜底引擎"),
        ("funasr", "增强", "funasr", "中文转写主引擎（缺了就退回 faster-whisper）"),
        ("kaldi_native_fbank", "增强", "kaldi-native-fbank", "FunASR 的 fbank 后端，缺了 funasr 会直接报错"),
        ("torch", "增强", "torch", "FunASR 运行时（CPU 版即可）"),
    ]
    for mod, level, pkg, why in mods:
        try:
            __import__(mod)
            check("依赖", mod, True, why, level="core" if level == "核心" else "extra")
        except Exception as e:
            check("依赖", mod, False, "%s ｜ 用途：%s" % (type(e).__name__, why),
                  "pip install %s" % pkg, level="core" if level == "核心" else "extra")


def binaries():
    import fetchers
    ff = fetchers.ffmpeg_bin()
    check("二进制", "ffmpeg", bool(ff), ff or "找不到",
          "下载 ffmpeg.exe 放到 KB-Studio/vendor/ffmpeg.exe，或加进 PATH（国内可用 ghfast.top 镜像）")
    wm = fetchers.WHISPER_MODEL
    check("模型", "faster-whisper-small", wm.exists(), str(wm),
          "首次转写会自动从 HuggingFace 下载；离线就手动放到该目录，或设 KB_WHISPER_MODEL")
    cache = Path.home() / ".cache" / "modelscope" / "models"
    have = []
    for key in ("paraformer", "fsmn-vad", "punc_ct"):
        if cache.exists():
            for p in cache.iterdir():
                if key in p.name.lower():
                    have.append(p.name.split("--")[-1])
                    break
    check("模型", "FunASR 三个模型", bool(have) or not cache.exists(),
          "、".join(sorted(set(have))) or "未下载（第一次转写时自动拉，约 1.1GB）",
          "python -c \"from funasr import AutoModel; [AutoModel(model=m, disable_update=True) for m in ('paraformer-zh','fsmn-vad','ct-punc')]\"",
          level="extra")


def vault():
    import server
    v = Path(server.VAULT)
    if not v.exists():
        check("笔记库", "vault 路径", False, str(v), "python server.py --vault D:/你的库路径")
        return
    counts = {}
    for sub in ("raw/clips", "wiki/concepts", "wiki/connections", "wiki/questions"):
        d = v / sub
        counts[sub] = len(list(d.glob("*.md"))) if d.exists() else 0
    check("笔记库", "vault 可读", True, "%s ｜ %s" % (v, counts), "")
    obs = v / ".obsidian" / "graph.json"
    check("笔记库", "Obsidian 图谱配置", obs.exists(), str(obs) if obs.exists() else "没有 .obsidian/graph.json",
          "用 Obsidian 打开这个文件夹一次即可生成")


def scripts():
    """B 站链路复用的脚本必须跟着仓库走，不能依赖某台机器上的 agent skill 目录。"""
    import fetchers
    d = next((x for x in fetchers.SCRIPT_CANDIDATES if (x / "bilibili.py").exists()), None)
    here = (HERE / "scripts" / "bilibili.py").exists()
    check("脚本", "bilibili.py 在项目内", here, str(d) if d else "哪都没找到",
          "从仓库恢复 KB-Studio/scripts/bilibili.py", level="core")


def secrets():
    import fetchers
    ck = sorted(p.stem for p in fetchers.COOKIE_DIR.glob("*.txt")) if fetchers.COOKIE_DIR.exists() else []
    check("凭据", "平台 cookie", bool(ck), "已存：" + ("、".join(ck) if ck else "无"),
          "网页「Cookie 通行证」里粘贴；抖音/小红书/知乎没有 cookie 一律读不到正文")
    cfg = HERE / "config.json"
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text(encoding="utf-8"))
            check("凭据", "模型 API", bool(c.get("api_key")), "model=%s" % c.get("model"),
                  "", level="extra")
        except Exception as e:
            check("凭据", "模型 API", False, "config.json 读不动：%s" % e, "参照 config.example.json 重建")
    else:
        check("凭据", "模型 API", None, "没有 config.json（会退回环境变量或 ~/.claude/settings.json）",
              "复制 config.example.json 为 config.json 并填 api_key", level="extra")
    gi = (HERE / ".gitignore").read_text(encoding="utf-8") if (HERE / ".gitignore").exists() else ""
    missing = [k for k in ("cookies/", "config.json", "uploads/", "media/") if k not in gi]
    check("凭据", ".gitignore 挡住凭据", not missing,
          "已挡：cookies/config.json/uploads/media" if not missing else "漏了：" + "、".join(missing),
          "开源前必须补齐，否则你的 cookie 和 key 会被 git 带走")


def network():
    """只做一次 TCP 探测，不发业务请求；不通就标「未测」而不是失败（用户可能离线）。"""
    for host, port, why in (("www.douyin.com", 443, "抖音"), ("www.zhihu.com", 443, "知乎"),
                            ("api.deepseek.com", 443, "DeepSeek")):
        try:
            socket.create_connection((host, port), timeout=3).close()
            check("网络", host, True, "%s 可达" % why, "")
        except Exception as e:
            check("网络", host, None, "%s（离线或墙）" % type(e).__name__,
                  "离线不算失败：有 cookie + 能连上时才能抓", level="extra")


def main():
    fix_hint = "--fix-hint" in sys.argv
    pyver()
    imports()
    try:
        binaries()
    except Exception as e:
        check("二进制", "fetchers 可导入", False, str(e)[:120], "pip install -r requirements.txt")
    try:
        vault()
    except Exception as e:
        check("笔记库", "server 可导入", False, str(e)[:120], "在 KB-Studio 目录下跑")
    scripts()
    secrets()
    network()

    order = {"运行环境": 0, "依赖": 1, "二进制": 2, "模型": 3, "笔记库": 4, "凭据": 5, "网络": 6}
    ROWS.sort(key=lambda r: (order.get(r["group"], 9), r["group"], not r["state"]))
    icon = {True: "✓", False: "✗", None: "·"}
    last = None
    bad = 0
    for r in ROWS:
        if r["group"] != last:
            print("\n[%s]" % r["group"])
            last = r["group"]
        if r["state"] is False and r["level"] == "core":
            bad += 1
        if r["state"] is False and r["level"] == "extra":
            print("  ~ %-22s %s" % (r["name"], r["detail"]))
        else:
            print("  %s %-22s %s" % (icon[r["state"]], r["name"], r["detail"]))
        if r["state"] is not True and fix_hint and r["hint"]:
            print("      ↳ %s" % r["hint"])
    core_missing = [r for r in ROWS if r["state"] is False and r["level"] == "core"]
    print("\n结论：%d 项检查，核心缺失 %d 项。" % (len(ROWS), len(core_missing)))
    if core_missing:
        print("先补这些："); [print("  -", r["name"], "→", r["hint"]) for r in core_missing]
        return 1
    print("核心能力齐了。带 ~ 的是可选项（缺了只是降级，不影响跑）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
