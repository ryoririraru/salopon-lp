#!/usr/bin/env python3
"""サロポン加盟募集リール動画ジェネレーター

episodes/*.json の台本（テロップ＋ナレーション）から
Instagram リール用の縦型動画（1080x1920 / MP4）を生成する。

  python3 reels/scripts/render.py            # 全エピソードを生成
  python3 reels/scripts/render.py ep01 ep02  # 指定エピソードのみ

必要なもの: ffmpeg / open_jtalk / naist-jdic / Noto Sans CJK / Pillow
（reels/scripts/setup.sh でまとめて入る）
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bgm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
OUTPUT = ROOT / "output"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

W, H = 1080, 1920
FPS = 30
# サロポンカラー（ロゴの黄色に合わせる）
YELLOW = (254, 229, 6)
YELLOW_DARK = (246, 214, 0)
BLACK = (17, 17, 17)
WHITE = (255, 255, 255)

FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
if not os.path.exists(FONT_BOLD):
    FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

DIC = "/var/lib/mecab/dic/open-jtalk/naist-jdic"
VOICE = str(ROOT / "voice" / "mei_normal.htsvoice")
LOGO_SRC = ROOT / "assets" / "salopon_logo.png"

# Instagram の UI（下部のキャプション・右側のボタン）に被らない安全領域
SAFE_TOP, SAFE_BOTTOM = 260, 1500
SAFE_LEFT, SAFE_RIGHT = 90, 930

_logo = None


def logo():
    """添付ロゴ（黄色地に黒）から黄色を抜いた透過ロゴを作る"""
    global _logo
    if _logo is None:
        src = Image.open(LOGO_SRC).convert("RGB")
        r = src.getchannel("R")
        alpha = r.point(lambda v: max(0, min(255, int((235 - v) * 255 / 180))))
        lg = Image.new("RGBA", src.size, BLACK + (0,))
        lg.putalpha(alpha)
        _logo = lg.crop(alpha.point(lambda v: 255 if v > 40 else 0).getbbox())
    return _logo


def paste_logo(img, width, cx, top):
    lg = logo()
    h = int(lg.height * width / lg.width)
    lg = lg.resize((width, h), Image.LANCZOS)
    img.paste(lg, (int(cx - width / 2), top), lg)
    return top + h


def font(path, size):
    # .ttc の index 0 = JP
    return ImageFont.truetype(path, size, index=0)


def parse_line(line):
    """[[強調]] を強調表示にする。[(text, accent), ...] を返す"""
    parts = []
    for i, seg in enumerate(re.split(r"\[\[|\]\]", line)):
        if seg:
            parts.append((seg, i % 2 == 1))
    return parts


def background():
    img = Image.new("RGB", (W, H), YELLOW)
    d = ImageDraw.Draw(img)
    # 斜めストライプ（ごく薄く）
    for x in range(-H, W, 90):
        d.line([(x, H), (x + H, 0)], fill=YELLOW_DARK, width=28)
    # 四隅のドット
    for cx, cy in ((0, H), (W, 0)):
        for gx in range(-360, 361, 44):
            for gy in range(-360, 361, 44):
                dist = (gx * gx + gy * gy) ** 0.5
                if dist < 360:
                    rad = 9 * (1 - dist / 360)
                    x, y = cx + gx, cy + gy
                    d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=BLACK)
    return img


def draw_header(img, idx, total, tag, show_logo=True):
    d = ImageDraw.Draw(img)
    bar_w = W - 2 * SAFE_LEFT
    d.rounded_rectangle((SAFE_LEFT, 120, SAFE_LEFT + bar_w, 134), 7, fill=(214, 190, 0))
    d.rounded_rectangle((SAFE_LEFT, 120, SAFE_LEFT + int(bar_w * (idx + 1) / total), 134), 7, fill=BLACK)
    if show_logo:
        paste_logo(img, 250, SAFE_LEFT + 125, 165)
    if tag:
        ft = font(FONT_BOLD, 34)
        tw = d.textlength(tag, font=ft)
        x1 = SAFE_LEFT + bar_w
        d.rounded_rectangle((x1 - tw - 52, 214, x1, 280), 33, fill=BLACK)
        d.text((x1 - tw - 26, 220), tag, font=ft, fill=YELLOW)


def fit_size(lines, max_w, start):
    size = start
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    while size > 40:
        f = font(FONT_BOLD, size)
        widest = max(d.textlength("".join(s for s, _ in parse_line(l)), font=f)
                     + 40 * sum(a for _, a in parse_line(l)) for l in lines)
        if widest <= max_w:
            return size
        size -= 4
    return size


def draw_telop(img, telop, size=None, area=(420, SAFE_BOTTOM)):
    d = ImageDraw.Draw(img)
    lines = telop.split("\n")
    size = fit_size(lines, SAFE_RIGHT - SAFE_LEFT + 20, size or 100)
    f = font(FONT_BOLD, size)
    lh = int(size * 1.55)
    total_h = lh * len(lines)
    y = (area[0] + area[1]) // 2 - total_h // 2
    pad = 20
    for line in lines:
        parts = parse_line(line)
        lw = sum(d.textlength(s, font=f) + (2 * pad if a else 0) for s, a in parts)
        x = (W - lw) / 2
        for s, accent in parts:
            sw = d.textlength(s, font=f)
            if accent:
                top = y + int(size * 0.12)
                d.rounded_rectangle((x, top, x + sw + 2 * pad, top + int(size * 1.28)), 18, fill=BLACK)
                d.text((x + pad, y), s, font=f, fill=YELLOW)
                x += sw + 2 * pad
            else:
                d.text((x, y), s, font=f, fill=BLACK)
                x += sw
        y += lh


def draw_cta(img, cta):
    d = ImageDraw.Draw(img)
    info = CONFIG["cta"][cta]
    bottom = paste_logo(img, 600, W / 2, 230)
    draw_telop(img, info["telop"], size=74, area=(bottom + 10, bottom + 380))
    # ボタン
    fb = font(FONT_BOLD, 56)
    label = info["button"]
    by = bottom + 420
    bw = d.textlength(label, font=fb) + 140
    bx = (W - bw) / 2
    d.rounded_rectangle((bx, by, bx + bw, by + 124), 62, fill=BLACK)
    d.text((bx + 70, by + 22), label + "", font=fb, fill=YELLOW)
    # URL
    url = info["url"]
    fu_size = 40
    while fu_size > 24 and d.textlength(url, font=font(FONT_REG, fu_size)) > W - 2 * 70:
        fu_size -= 2
    fu = font(FONT_REG, fu_size)
    uw = d.textlength(url, font=fu)
    uy = by + 170
    d.rounded_rectangle((40, uy, W - 40, uy + 120), 24, fill=WHITE, outline=BLACK, width=6)
    d.text(((W - uw) / 2, uy + 60 - fu_size * 0.62), url, font=fu, fill=BLACK)
    fs = font(FONT_REG, 36)
    note = "▲ プロフィールのリンクからもアクセスできます"
    d.text(((W - d.textlength(note, font=fs)) / 2, uy + 150), note, font=fs, fill=BLACK)


def tts(text, out_wav):
    subprocess.run(
        ["open_jtalk", "-x", DIC, "-m", VOICE, "-r", str(CONFIG.get("speech_rate", 1.1)),
         "-fm", str(CONFIG.get("pitch_shift", 0)), "-ow", str(out_wav)],
        input=text.encode("utf-8"), check=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def render_episode(ep_path):
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    scenes = ep["scenes"] + [{"cta": ep["cta"], "narration": CONFIG["cta"][ep["cta"]]["narration"]}]
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        segs = []
        starts = []
        t_acc = 0.0
        for i, sc in enumerate(scenes):
            img = background()
            draw_header(img, i, len(scenes), "" if "cta" in sc else ep.get("tag", ""), show_logo="cta" not in sc)
            if "cta" in sc:
                draw_cta(img, sc["cta"])
            else:
                draw_telop(img, sc["telop"], size=sc.get("size"))
            png = tmp / f"s{i:02d}.png"
            img.save(png)
            wav = tmp / f"s{i:02d}.wav"
            tts(sc.get("yomi", sc["narration"]), wav)
            dur = duration(wav) + 0.35
            if "cta" in sc:
                dur = max(dur + 1.5, 5.0)
            seg = tmp / f"s{i:02d}.mp4"
            fade_out = max(dur - 0.15, 0)
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-framerate", str(FPS), "-i", str(png),
                "-i", str(wav),
                "-filter_complex",
                f"[0:v]scale={W}:{H},zoompan=z='min(1+0.0004*on,1.04)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={W}x{H}:fps={FPS},fade=t=in:st=0:d=0.15,fade=t=out:st={fade_out:.2f}:d=0.15,format=yuv420p[v];"
                f"[1:a]aresample=44100,apad[a]",
                "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2", str(seg)], check=True)
            segs.append(seg)
            starts.append(t_acc)
            t_acc += duration(seg)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{s}'\n" for s in segs))
        out = OUTPUT / f"{ep_path.stem}.mp4"
        voice_only = tmp / "voice_only.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(voice_only)], check=True)
        # BGM を生成して、ナレーションの下に敷く（声が鳴っている間は自動で下げる）
        total = duration(voice_only)
        b = {**CONFIG["bgm_default"], **ep.get("bgm", {})}
        bgm_wav = tmp / "bgm.wav"
        bgm.write_wav(bgm_wav, bgm.make_track(total, b["style"], b["bpm"], b["key"], b["prog"],
                                              b.get("seed", 0), cues=starts[1:], cta_at=starts[-1]))
        vol = CONFIG.get("bgm_volume", 0.32)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(voice_only), "-i", str(bgm_wav),
            "-filter_complex",
            f"[0:a]asplit=2[n][sc];[1:a]volume={vol}[b];"
            "[b][sc]sidechaincompress=threshold=0.02:ratio=5:attack=20:release=400[bd];"
            "[n]volume=1.6[nv];[nv][bd]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out)], check=True)
        # サムネイル（カバー画像）＝ 1シーン目
        Image.open(tmp / "s00.png").save(OUTPUT / f"{ep_path.stem}_cover.png")
    write_caption(ep_path.stem, ep)
    print(f"✔ {out.relative_to(ROOT.parent)}  ({duration(out):.1f}秒)")


def write_caption(stem, ep):
    info = CONFIG["cta"][ep["cta"]]
    text = (
        f"{ep['caption']}\n\n"
        f"▼{info['button']}\n{info['url']}\n"
        f"（プロフィールのリンクからもどうぞ）\n\n"
        f"{' '.join(CONFIG['hashtags'] + ep.get('hashtags', []))}\n\n"
        f"ナレーション音声: HTS Voice \"Mei\" (CC BY 3.0, Nagoya Institute of Technology)\n"
    )
    (OUTPUT / f"{stem}_caption.txt").write_text(text, encoding="utf-8")


def main():
    names = sys.argv[1:]
    files = sorted(EPISODES.glob("*.json"))
    if names:
        files = [f for f in files if f.stem in names or f.stem.split("_")[0] in names]
    for f in files:
        render_episode(f)


if __name__ == "__main__":
    main()
