#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili.py — B站视频内容获取（元信息 + 字幕/转写），输出带时间戳的 Markdown

链路：
  1. 拿 buvid3/buvid4 cookie（可叠加用户 SESSDATA，见 BILI_SESSDATA 环境变量）
  2. wbi 签名后调 view 接口拿元信息（标题/UP主/简介/aid/cid/时长）
  3. 调 player/v2 拿字幕列表（含 AI 字幕）
  4. 有字幕 -> 下载字幕 JSON，转成带时间戳段落
     无字幕 -> yt-dlp 提取音频 + faster-whisper 转写，生成带时间戳段落

时间戳是可点击的 B站跳转链接（?t=<秒>），满足「回头跳关键帧」的需求。

单独用法:
  python bilibili.py BV1Dvut6sEa5
  python bilibili.py "https://www.bilibili.com/video/BV1Dvut6sEa5" --block 45
  python bilibili.py BV1Dvut6sEa5 --transcribe          # 强制走音频转写
  python bilibili.py BV1Dvut6sEa5 --model medium        # 指定 whisper 模型

作为模块:
  from bilibili import fetch
  info = fetch("BV1Dvut6sEa5")
"""
import sys
import os
import re
import json
import time
import hashlib
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_PLAYER = "https://api.bilibili.com/x/player/v2"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
# ffmpeg 所在目录（供 yt-dlp 使用）：优先 kb-tools/bin
DEFAULT_FFMPEG_BIN = os.path.join(
    os.path.expanduser("~"), "kb-tools", "bin")

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliError(Exception):
    pass


class Session:
    """轻量会话：自己维护 Cookie（仅内存，不落盘），带 UA/Referer 反风控头"""

    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})

    def _absorb(self, headers):
        for raw in headers.get_all("Set-Cookie") or []:
            first = raw.split(";", 1)[0].strip()
            if "=" in first:
                k, v = first.split("=", 1)
                self.cookies.setdefault(k.strip(), v.strip())

    def _headers(self, referer, want_json=True):
        h = {
            "User-Agent": UA,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "application/json, text/plain, */*" if want_json else "*/*",
        }
        if self.cookies:
            h["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        return h

    def get_json(self, url, referer="https://www.bilibili.com/", timeout=25):
        req = urllib.request.Request(url, headers=self._headers(referer, True))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self._absorb(resp.headers)
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw.decode("utf-8", "replace")}

    def get_bytes(self, url, referer="https://www.bilibili.com/", timeout=30):
        req = urllib.request.Request(url, headers=self._headers(referer, False))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self._absorb(resp.headers)
            return resp.read()

    def warm(self):
        """访问首页拿 buvid3/buvid4，缺失时本地生成兜底值"""
        try:
            self.get_bytes("https://www.bilibili.com/", timeout=20)
        except Exception:
            pass
        if "buvid3" not in self.cookies:
            import uuid
            import random
            fake = str(uuid.UUID(int=random.getrandbits(128))) + "infoc"
            self.cookies["buvid3"] = fake
            self.cookies["buvid4"] = fake
        sessdata = os.environ.get("BILI_SESSDATA", "").strip()
        if sessdata:
            self.cookies["SESSDATA"] = sessdata
        return self


def _mixin_key(orig):
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _wbi_sign(params, img_key, sub_key):
    params = dict(params)
    params["wts"] = int(time.time())
    filtered = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
                for k, v in params.items()}
    query = urllib.parse.urlencode(sorted(filtered.items()))
    return query + "&w_rid=" + hashlib.md5(
        (query + _mixin_key(img_key + sub_key)).encode()).hexdigest()


def _wbi_keys(sess):
    try:
        data = sess.get_json(API_NAV)
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_url, sub_url = wbi.get("img_url", ""), wbi.get("sub_url", "")
        if not img_url or not sub_url:
            return None, None
        return (img_url.rsplit("/", 1)[-1].split(".")[0],
                sub_url.rsplit("/", 1)[-1].split(".")[0])
    except Exception:
        return None, None


def _signed_url(sess, base, params):
    """拼 wbi 签名 URL；签名不可用时退化成普通拼接"""
    ik, sk = _wbi_keys(sess)
    if ik and sk:
        return base + "?" + _wbi_sign(params, ik, sk)
    return base + "?" + urllib.parse.urlencode(params)


def extract_bvid(text):
    """从 URL 或纯文本中提取 BV 号 / av 号"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:[?&/]|^)av(\d+)", text)
    if m:
        return "av" + m.group(1)
    raise BiliError("无法从输入中识别 B站视频 ID：%s" % text)


def _fmt_ts(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


def _blocks(segments, block):
    """[(start, text)] -> 按 block 秒合并成段落，减少碎片"""
    out, cur_start, cur_txt = [], None, []
    for start, txt in segments:
        txt = (txt or "").strip()
        if not txt:
            continue
        if cur_start is None:
            cur_start = start
        if start - cur_start > block and cur_txt:
            out.append((cur_start, " ".join(cur_txt)))
            cur_start, cur_txt = start, []
        cur_txt.append(txt)
    if cur_txt:
        out.append((cur_start, " ".join(cur_txt)))
    return out


def _blocks_to_md(blocks, bvid):
    lines = []
    for start, txt in blocks:
        t = int(start)
        link = "https://www.bilibili.com/video/%s?t=%d" % (bvid, t)
        lines.append("- [`%s`](%s) %s" % (_fmt_ts(start), link, txt))
    return "\n".join(lines)


def _yt_dlp_cmd():
    """优先用 PATH 里的 yt-dlp 命令，其次用 python 模块"""
    import shutil
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        raise BiliError("未找到 yt-dlp。请先 `pip install yt-dlp`")


def _download_audio_api(sess, bvid, cid, workdir):
    """
    走 B站 playurl 接口直接取 dash 音频流（不需要 ffmpeg，避开 yt-dlp 的 412 风控）。
    返回 (文件路径, 码率kbps)
    """
    data = sess.get_json(_signed_url(sess, API_PLAYURL, {
        "bvid": bvid, "cid": cid, "fnval": "16", "fnver": "0", "fourk": "0"}),
        referer="https://www.bilibili.com/video/%s" % bvid)
    if data.get("code") != 0:
        raise BiliError("playurl 返回 %s：%s" % (data.get("code"), data.get("message")))
    audios = ((data.get("data") or {}).get("dash") or {}).get("audio") or []
    if not audios:
        raise BiliError("playurl 未返回音频流（可能需登录，可设 BILI_SESSDATA）")
    audios.sort(key=lambda a: a.get("bandwidth", 0), reverse=True)
    pick = audios[0]
    dest = os.path.join(workdir, "%s.m4a" % bvid)
    raw = sess.get_bytes(pick["baseUrl"],
                         referer="https://www.bilibili.com/video/%s" % bvid,
                         timeout=300)
    with open(dest, "wb") as f:
        f.write(raw)
    return dest, int(pick.get("bandwidth", 0) // 1000)


def _download_audio_ytdlp(bvid, workdir):
    """用 yt-dlp 提取音频（备选路径），返回音频文件路径"""
    import subprocess
    out_tpl = os.path.join(workdir, "%s.%%(ext)s" % bvid)
    cmd = _yt_dlp_cmd() + [
        "-f", "bestaudio/best",
        "-o", out_tpl,
        "--no-playlist", "--no-warnings", "--retries", "3",
        "https://www.bilibili.com/video/%s" % bvid]
    env = os.environ.copy()
    # 让 yt-dlp 能找到 ffmpeg（kb-tools/bin 或 skill 自带）
    bin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
    extra = os.pathsep.join([p for p in (bin_dir, DEFAULT_FFMPEG_BIN) if p and os.path.isdir(p)])
    if extra:
        env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise BiliError("yt-dlp 下载音频失败：%s"
                        % (proc.stderr or proc.stdout or "")[-600:])
    cands = [f for f in os.listdir(workdir) if f.startswith(bvid)]
    if not cands:
        raise BiliError("yt-dlp 未产出音频文件")
    cands.sort(key=lambda f: os.path.getmtime(os.path.join(workdir, f)), reverse=True)
    return os.path.join(workdir, cands[0])


def _resolve_model(model_size):
    """
    优先用本地预置模型目录（~/kb-tools/models/faster-whisper-<size>），
    避免每次联网拉模型；本地没有才交给 faster-whisper 自行下载。
    """
    if os.path.isdir(model_size):
        return model_size
    local = os.path.join(os.path.expanduser("~"), "kb-tools", "models",
                         "faster-whisper-%s" % model_size)
    if os.path.isfile(os.path.join(local, "model.bin")):
        return local
    return model_size


def _transcribe_funasr(audio_path):
    """优先用 KB Studio 那套 FunASR（fsmn-vad + paraformer-zh）：同一条 2 分钟中文音频，
    faster-whisper small 把「爆款/场景/信息流/带货」听成「报款/常理/星系流/代货」，FunASR 全对。
    拿不到就返回 None，让调用方退回本文件自带的 whisper 路径。"""
    try:
        import subprocess
        import tempfile
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import fetchers
        ff = fetchers.ffmpeg_bin()
        if not ff:
            return None
        wav = os.path.join(tempfile.gettempdir(), "bili_%s.wav" % os.getpid())
        subprocess.run([ff, "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", wav],
                       capture_output=True, timeout=600, check=True)
        paras = fetchers._transcribe_funasr(wav)
        try:
            os.remove(wav)
        except OSError:
            pass
        return [(a, t) for a, b, t in paras] if paras else None
    except Exception:
        return None


def _transcribe(audio_path, model_size="small", lang="zh"):
    """转写，返回 ([(start, text), ...], 引擎名)。先试 FunASR，再退回 faster-whisper。
    引擎名要落到原料的 method 字段里 —— 两家中文准确率各有胜负（见 agent.md §19），
    事后得能看出这句是哪个模型听出来的。"""
    got = _transcribe_funasr(audio_path)
    if got:
        return got, "FunASR(paraformer-zh) 转写"
    try:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from faster_whisper import WhisperModel
    except ImportError:
        raise BiliError("未安装 faster-whisper，请先 `pip install faster-whisper`")
    model = WhisperModel(_resolve_model(model_size), device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio_path, language=lang, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500))
    return ([(seg.start, seg.text.strip()) for seg in segments if seg.text.strip()],
            "faster-whisper(%s) 转写" % model_size)


def fetch(video_id, block=45, force_transcribe=False, model="small",
          workdir=None, keep_audio=False):
    """
    获取 B站视频内容。
    返回 dict: {platform, bvid, aid, cid, title, author, url, duration,
                published, description, tags, text, method}
    """
    bvid = extract_bvid(video_id)
    sess = Session().warm()

    q = {"bvid": bvid} if bvid.startswith("BV") else {"aid": bvid[2:]}
    data = sess.get_json(_signed_url(sess, API_VIEW, q))
    if data.get("code") != 0:
        raise BiliError(
            "view 接口返回 %s：%s\n多半是风控。解决：浏览器登录 B站后，把 Cookie 里的 "
            "SESSDATA 设到环境变量 BILI_SESSDATA 再跑（仅本地内存使用，不写笔记）。"
            % (data.get("code"), data.get("message")))
    d = data["data"]
    cid = d.get("cid")
    real_bvid = d.get("bvid") or bvid
    info = {
        "platform": "B站",
        "bvid": real_bvid,
        "aid": d.get("aid"),
        "cid": cid,
        "title": d.get("title", ""),
        "author": (d.get("owner") or {}).get("name", ""),
        "url": "https://www.bilibili.com/video/%s" % real_bvid,
        "duration": d.get("duration", 0),
        "published": time.strftime("%Y-%m-%d", time.localtime(d.get("pubdate", 0))),
        "description": (d.get("desc") or "").strip(),
        "tags": d.get("tname", ""),
        "view": d.get("stat", {}).get("view", 0),
    }

    # --- 优先走官方字幕（含 AI 字幕） ---
    text, method = "", None
    if not force_transcribe and cid:
        try:
            pd_ = sess.get_json(_signed_url(
                sess, API_PLAYER, {"aid": d["aid"], "cid": cid}))
            subs = (((pd_.get("data") or {}).get("subtitle") or {}).get("subtitles")) or []
            if subs:
                pick = None
                for pref in ("zh-CN", "zh-Hans", "zh"):
                    pick = next((s for s in subs if s.get("lan", "").startswith(pref)), None)
                    if pick:
                        break
                pick = pick or next((s for s in subs if "ai-" in s.get("lan", "")), subs[0])
                sub_url = pick.get("subtitle_url", "")
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url
                body = json.loads(sess.get_bytes(sub_url).decode("utf-8")).get("body", [])
                segs = [(float(i.get("from", 0)), i.get("content", "")) for i in body]
                text = _blocks_to_md(_blocks(segs, block), real_bvid)
                method = "官方字幕(%s)" % pick.get("lan_doc", pick.get("lan", ""))
        except Exception:
            text, method = "", None

    # --- 无字幕：音频转写 ---
    if not text:
        import tempfile
        workdir = workdir or tempfile.mkdtemp(prefix="bili_")
        os.makedirs(workdir, exist_ok=True)
        audio = None
        last_err = None
        for getter in (lambda: _download_audio_api(sess, real_bvid, cid, workdir)[0],
                       lambda: _download_audio_ytdlp(real_bvid, workdir)):
            try:
                audio = getter()
                break
            except BiliError as e:
                last_err = e
        if audio is None:
            raise last_err
        try:
            segs, engine = _transcribe(audio, model_size=model)
            text = _blocks_to_md(_blocks(segs, block), real_bvid)
            method = engine
        finally:
            if not keep_audio:
                try:
                    os.remove(audio)
                except OSError:
                    pass

    info["text"] = text
    info["method"] = method
    return info


def main():
    import argparse
    ap = argparse.ArgumentParser(description="B站视频内容获取（字幕优先，无字幕转写）")
    ap.add_argument("video", help="BV号 / av号 / B站视频链接")
    ap.add_argument("--block", type=int, default=45, help="时间戳分段秒数，默认 45")
    ap.add_argument("--transcribe", action="store_true", help="忽略官方字幕，强制音频转写")
    ap.add_argument("--model", default="small", help="whisper 模型，默认 small")
    ap.add_argument("--workdir", default=None, help="音频临时目录")
    ap.add_argument("--keep-audio", action="store_true", help="保留下载的音频")
    args = ap.parse_args()

    try:
        info = fetch(args.video, block=args.block, force_transcribe=args.transcribe,
                     model=args.model, workdir=args.workdir, keep_audio=args.keep_audio)
    except BiliError as e:
        print("❌ %s" % e, file=sys.stderr)
        sys.exit(1)

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
