# -*- coding: utf-8 -*-
"""一键装：依赖 → 建 cookies/ → 体检。跨平台，只用标准库做安装。

    python install.py            # 只装核心（网页 / 公众号 / 知乎 / 小红书）
    python install.py --video    # 再加 yt-dlp + faster-whisper（B站 / 抖音要）

每一步失败都继续往下走，最后统一报「现在能干什么、还缺什么」。
不碰任何人的 cookie，也不替你写 API key。
"""
import argparse
import importlib
import io
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PIP_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"


def run(cmd, why, timeout=1800):
    print("· %s" % why)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print("  没成：%s" % ((p.stderr or p.stdout or "").strip()[-300:]))
            return False
        print("  好了")
        return True
    except Exception as e:
        print("  没成：%s" % e)
        return False


def have(mod):
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def ffmpeg_ready():
    return bool(shutil.which("ffmpeg"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", action="store_true", help="装视频通道（yt-dlp + faster-whisper）")
    a = ap.parse_args()

    py = sys.version_info
    print("Python %d.%d.%d ｜ %s" % (py.major, py.minor, py.micro, platform.system()))
    if (py.major, py.minor) < (3, 10):
        print("！这套要 Python 3.10 以上，现在是 %d.%d，先升级再装" % (py.major, py.minor))
        return 1

    pip = [sys.executable, "-m", "pip", "install", "-q", "-i", PIP_MIRROR]
    run(pip + ["-r", str(HERE / "requirements.txt")], "装核心依赖（requests / beautifulsoup4）")
    if a.video:
        run(pip + ["yt-dlp", "faster-whisper"], "装视频通道（yt-dlp + faster-whisper）")
        if not ffmpeg_ready():
            print("· 没找到 ffmpeg —— 没有它转写跑不了。")
            print("  Windows：winget install Gyan.FFmpeg ；macOS：brew install ffmpeg")
            print("  或者跑一次 python -c \"import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())\"")

    (HERE / "cookies").mkdir(exist_ok=True)
    (HERE / "out").mkdir(exist_ok=True)

    print("\n体检：现在能读哪些平台")
    core = have("requests") and have("bs4")
    rows = [("网页 / 博客 / 公众号", core, ""),
            ("知乎", core, "需要 cookie：cookies/zhihu.txt"),
            ("小红书", core, "需要 cookie：cookies/xiaohongshu.txt"),
            ("抖音", core and have("yt_dlp"), "需要 cookie + yt-dlp"),
            ("B站", core and have("yt_dlp"), "有字幕最好；没字幕要 ffmpeg + faster-whisper"),
            ("Instagram / Facebook", core, "需要 cookie")]
    for name, ok, note in rows:
        print("  %s %-22s %s" % ("✅" if ok else "❌", name, note))

    key = None
    if (HERE / "extract-config.json").is_file():
        try:
            key = json.loads((HERE / "extract-config.json").read_text(encoding="utf-8")).get("api_key")
        except Exception:
            key = None
    if not key:
        key = __import__("os").environ.get("DEEPSEEK_API_KEY")
    if not key:
        try:
            tok = (json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
                   .get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", "")
            key = "" if tok.startswith("sk-ant-") else tok
        except Exception:
            key = None
    print("\n  %s" % ("✅ 已找到模型 key，--digest 可以凝练总结" if key
                     else "— 没有模型 key：不加 --digest 就完全不需要，抓取照跑"))
    print("\n下一步：python extract.py \"<一条链接>\"")
    print("需要登录态的平台：浏览器登录后 F12 → Network → 复制 Cookie 整串，"
          "存成 cookies/<站点>.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
