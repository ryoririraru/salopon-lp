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

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
OUTPUT = ROOT / "output"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

W, H = 1080, 1920
FPS = 30
NAVY = (13, 42, 87)
NAVY_DARK = (6, 22, 48)
BLUE = (59, 130, 246)
YELLOW = (245, 158, 11)
WHITE = (255, 255, 255)

FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
if not os.path.exists(FONT_BOLD):
    FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

DIC = "/var/lib/mecab/dic/open-jtalk/naist-jdic"
VOICE = str(ROOT / "voice" / "mei_normal.htsvoice")

# Instagram の UI（下部のキャプション・右側のボタン）に被らない安全領域
SAFE_TOP, SAFE_BOTTOM = 260, 1500
SAFE_LEFT, SAFE_RIGHT = 90, 930


def font(path, size):
    # .ttc の index 0 = JP
    return ImageFont.truetype(path, size, index=0)


def parse_line(line):
    """[[強調]] をアクセント色にする。[(text, accent), ...] を返す"""
    parts = []
    for i, seg in enumerate(re.split(r"\[\[|\]\]", line)):
        if seg:
            parts.append((seg, i % 2 == 1))
    return parts


def gradient_bg():
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(NAVY[k] * (1 - t) + NAVY_DARK[k] * t) for k in range(3))
        d.line([(0, y), (W, y)], fill=c)
    # 装飾の円
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.ellipse((-260, -200, 520, 580), fill=(59, 130, 246, 40))
    od.ellipse((640, 1380, 1400, 2140), fill=(59, 130, 246, 28))
    img.paste(ov, (0, 0), ov)
    return img


def draw_header(img, idx, total, tag):
    d = ImageDraw.Draw(img)
    # 進行バー
    bar_w = W - 2 * SAFE_LEFT
    d.rounded_rectangle((SAFE_LEFT, 150, SAFE_LEFT + bar_w, 162), 6, fill=(60, 82, 120))
    d.rounded_rectangle((SAFE_LEFT, 150, SAFE_LEFT + int(bar_w * (idx + 1) / total), 162), 6, fill=YELLOW)
    # ブランド
    f = font(FONT_BOLD, 46)
    d.text((SAFE_LEFT, 190), "サロポン", font=f, fill=WHITE)
    if tag:
        ft = font(FONT_REG, 30)
        tw = d.textlength(tag, font=ft)
        x1 = SAFE_LEFT + bar_w
        d.rounded_rectangle((x1 - tw - 40, 192, x1, 246), 27, fill=BLUE)
        d.text((x1 - tw - 20, 199), tag, font=ft, fill=WHITE)


def fit_size(lines, max_w, start):
    size = start
    while size > 40:
        f = font(FONT_BOLD, size)
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        widest = max(d.textlength("".join(s for s, _ in parse_line(l)), font=f) for l in lines)
        if widest <= max_w:
            return size
        size -= 4
    return size


def draw_telop(img, telop, size=None, area=(SAFE_TOP + 60, SAFE_BOTTOM)):
    d = ImageDraw.Draw(img)
    lines = telop.split("\n")
    size = fit_size(lines, SAFE_RIGHT - SAFE_LEFT, size or 96)
    f = font(FONT_BOLD, size)
    lh = int(size * 1.45)
    total_h = lh * len(lines)
    y = (area[0] + area[1]) // 2 - total_h // 2
    for line in lines:
        parts = parse_line(line)
        lw = sum(d.textlength(s, font=f) for s, _ in parts)
        x = (W - lw) / 2
        for s, accent in parts:
            sw = d.textlength(s, font=f)
            d.text((x + 4, y + 5), s, font=f, fill=(0, 0, 0))  # 影
            d.text((x, y), s, font=f, fill=YELLOW if accent else WHITE)
            x += sw
        y += lh


def draw_cta(img, cta):
    d = ImageDraw.Draw(img)
    info = CONFIG["cta"][cta]
    draw_telop(img, info["telop"], size=84, area=(SAFE_TOP + 40, 900))
    # ボタン
    fb = font(FONT_BOLD, 54)
    label = info["button"]
    bw = d.textlength(label, font=fb) + 120
    bx = (W - bw) / 2
    d.rounded_rectangle((bx, 960, bx + bw, 1080), 60, fill=YELLOW)
    d.text((bx + 60, 984), label, font=fb, fill=NAVY_DARK)
    # URL
    url = info["url"]
    fu_size = 40
    while fu_size > 24 and d.textlength(url, font=font(FONT_REG, fu_size)) > W - 2 * 60:
        fu_size -= 2
    fu = font(FONT_REG, fu_size)
    uw = d.textlength(url, font=fu)
    d.rounded_rectangle((40, 1140, W - 40, 1260), 24, fill=WHITE)
    d.text(((W - uw) / 2, 1200 - fu_size * 0.62), url, font=fu, fill=NAVY)
    fs = font(FONT_REG, 36)
    note = "▲ プロフィールのリンクからもアクセスできます"
    d.text(((W - d.textlength(note, font=fs)) / 2, 1300), note, font=fs, fill=WHITE)


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
        for i, sc in enumerate(scenes):
            img = gradient_bg()
            draw_header(img, i, len(scenes), ep.get("tag", ""))
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
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{s}'\n" for s in segs))
        out = OUTPUT / f"{ep_path.stem}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
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
