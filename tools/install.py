# -*- coding: utf-8 -*-
"""新机器一键装齐：依赖 → ffmpeg → 模型 → 体检。跨平台（Windows / macOS / Linux）。

    python tools/install.py            # 只装核心（抓取 + 网页工具 + whisper 兜底）
    python tools/install.py --asr      # 再加中文转写主引擎 FunASR（torch CPU 版）
    python tools/install.py --all      # 全都要

设计：每一步失败都继续往下走，最后由 doctor 统一报「现在能干什么、还缺什么」。
不会碰他的 cookies/ 和 config.json。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
VENDOR = HERE / "vendor"
MODELS = HERE / "models"


def run(cmd, why, timeout=1800):
    print("  $ %s" % " ".join(str(c) for c in cmd)[:110])
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout)
        if p.returncode != 0:
            print("    ✗ %s 失败：%s" % (why, (p.stderr or p.stdout)[-400:].decode("utf-8", "replace")))
            return False
        print("    ✓ %s" % why)
        return True
    except Exception as e:
        print("    ✗ %s：%s" % (why, e))
        return False


def pip(pkgs, why, index=None):
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs
    if index:
        cmd += ["--index-url", index]
    return run(cmd, why, timeout=3600)


def ffmpeg():
    if shutil.which("ffmpeg"):
        print("  ✓ 系统里已有 ffmpeg，跳过")
        return True
    target = VENDOR / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if target.exists():
        print("  ✓ vendor/ 里已有 ffmpeg")
        return True
    if os.name != "nt":
        print("  ! 非 Windows 请自行安装：brew install ffmpeg / apt install ffmpeg")
        return False
    VENDOR.mkdir(exist_ok=True)
    # 国内直连 GitHub release 常超时，先试 ghfast.top 镜像（台账 §14 记过这个路子）
    url = ("https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/"
           "latest/ffmpeg-master-latest-win64-gpl.zip")
    zip_path = VENDOR / "ffmpeg.zip"
    print("  下载 ffmpeg（约 90MB，走 ghfast.top 镜像）…")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kb-studio"})
        with urllib.request.urlopen(req, timeout=600) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            member = [n for n in z.namelist() if n.endswith("bin/ffmpeg.exe")]
            if not member:
                print("    ✗ 压缩包里没找到 bin/ffmpeg.exe")
                return False
            with z.open(member[0]) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        zip_path.unlink(missing_ok=True)
        print("    ✓ 装到 %s" % target)
        return True
    except Exception as e:
        print("    ✗ 下载失败：%s" % e)
        print("      手动办法：下一个 ffmpeg.exe 放进 %s" % VENDOR)
        return False


def whisper_model():
    dst = MODELS / "faster-whisper-small"
    if dst.exists():
        print("  ✓ 模型已在 %s" % dst)
        return True
    print("  下载 faster-whisper small（约 460MB，HuggingFace）…")
    return run([sys.executable, "-c",
                "import os;os.environ['HF_HOME']=r'%s';"
                "from huggingface_hub import snapshot_download;"
                "snapshot_download('Systran/faster-whisper-small',"
                "local_dir=r'%s')" % (MODELS, dst)], "拉 whisper 模型", timeout=3600)


def funasr_models():
    print("  拉 FunASR 三个模型（paraformer / vad / punc，约 1.1GB，缓存到用户目录）…")
    code = ("from funasr import AutoModel;"
            "[AutoModel(model=m, disable_update=True) for m in "
            "('paraformer-zh','fsmn-vad','ct-punc')];print('loaded')")
    return run([sys.executable, "-c", code], "预热 FunASR 模型", timeout=3600)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr", action="store_true", help="装中文转写主引擎 FunASR")
    ap.add_argument("--all", action="store_true", help="核心 + FunASR 全装")
    args = argparse.Namespace(asr=args.asr or args.all, all=args.all)

    print("Python %s ｜ %s\n" % (platform.python_version(), platform.system()))

    print("[1/4] 核心依赖（网页抓取 / PDF / 图片 / 语音兜底）")
    pip(["-r", str(HERE / "requirements.txt")], "核心依赖")

    print("\n[2/4] ffmpeg（音视频都要用它抽轨 / 抽帧）")
    ffmpeg()

    print("\n[3/4] 模型")
    whisper_model()
    if args.asr:
        print("\n[3b] FunASR：中文转写主引擎（不装也能跑，会退回 whisper）")
        if pip(["torch", "--index-url", "https://download.pytorch.org/whl/cpu"], "torch CPU 版"):
            pip(["funasr", "kaldi-native-fbank", "modelscope"], "funasr 及 fbank 后端")
            funasr_models()
    else:
        print("\n     跳过 FunASR。以后想装：python tools/install.py --asr")

    print("\n[4/4] 能力体检")
    subprocess.run([sys.executable, str(HERE / "tools" / "doctor.py"), "--fix-hint"])
    print("\n下一步：双击「启动知识库.bat」，浏览器开 http://127.0.0.1:8777，"
          "在「Cookie 通行证」里贴你自己的抖音/小红书/知乎 cookie。")


if __name__ == "__main__":
    main()
