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
import math
import wave
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
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



# ======================================================================
# 背景・ヘッダー
# ======================================================================
STRIPE = 90  # ストライプ周期（px）


def background_wide():
    """横に1周期ぶん広い背景（ずらして流れるストライプにする）"""
    w = W + STRIPE
    img = Image.new("RGB", (w, H), YELLOW)
    d = ImageDraw.Draw(img)
    for x in range(-H, w + STRIPE, STRIPE):
        d.line([(x, H), (x + H, 0)], fill=YELLOW_DARK, width=28)
    return img


def dots_overlay():
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for cx, cy in ((0, H), (W, 0)):
        for gx in range(-360, 361, 44):
            for gy in range(-360, 361, 44):
                dist = (gx * gx + gy * gy) ** 0.5
                if dist < 360:
                    rad = 9 * (1 - dist / 360)
                    x, y = cx + gx, cy + gy
                    d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=BLACK + (255,))
    return ov


def tag_sprite(tag):
    ft = font(FONT_BOLD, 34)
    tw = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(tag, font=ft))
    sp = Image.new("RGBA", (tw + 52, 66), (0, 0, 0, 0))
    d = ImageDraw.Draw(sp)
    d.rounded_rectangle((0, 0, tw + 51, 65), 33, fill=BLACK)
    d.text((26, 6), tag, font=ft, fill=YELLOW)
    return sp


def logo_sprite(width):
    lg = logo()
    return lg.resize((width, int(lg.height * width / lg.width)), Image.LANCZOS)


# ======================================================================
# イージング
# ======================================================================
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def ease_out_back(p, s=2.2):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def ease_out_elastic(p):
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return 2 ** (-10 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi / 3)) + 1


# ======================================================================
# スプライト（動かす部品）
# ======================================================================
class Item:
    """1つの部品。anim(t) -> (dx, dy, scale, alpha) で動きを決める"""

    def __init__(self, sprite, x, y, anim, redraw=None):
        self.sprite = sprite        # RGBA
        self.x, self.y = x, y       # 静止時の左上座標
        self.anim = anim
        self.redraw = redraw        # t -> sprite（カウントアップ用）
        self.cache = {}

    def draw(self, frame, t):
        dx, dy, sc, al = self.anim(t)
        if al <= 0.01 or sc <= 0.01:
            return
        sp = self.redraw(t) if self.redraw else self.sprite
        if abs(sc - 1) > 0.004:
            key = (id(sp), round(sc, 3))
            if key not in self.cache:
                if len(self.cache) > 400:
                    self.cache.clear()
                self.cache[key] = sp.resize((max(1, int(sp.width * sc)), max(1, int(sp.height * sc))),
                                            Image.BILINEAR)
            sp2 = self.cache[key]
        else:
            sp2 = sp
        if al < 0.99:
            a = sp2.getchannel("A").point(lambda v, al=al: int(v * al))
            sp2 = sp2.copy()
            sp2.putalpha(a)
        cx = self.x + self.sprite.width / 2 + dx
        cy = self.y + self.sprite.height / 2 + dy
        frame.alpha_composite(sp2, (int(cx - sp2.width / 2), int(cy - sp2.height / 2)))


def anim_slide_in(delay, dist=70, dur=0.38, exit_at=None):
    def f(t):
        p = clamp((t - delay) / dur)
        e = ease_out_cubic(p)
        dy = (1 - e) * dist
        al = clamp((t - delay) / (dur * 0.6))
        sc = 0.9 + 0.1 * e
        if exit_at is not None and t > exit_at:
            q = clamp((t - exit_at) / 0.18)
            dy -= 40 * q
            al *= 1 - q
        return 0, dy, sc, al
    return f


def anim_stamp(delay, dur=0.42, pulse=True, exit_at=None):
    """強調ワード：大きく「ドン！」と押されて、その後ゆっくり脈打つ"""
    def f(t):
        p = clamp((t - delay) / dur)
        if p <= 0:
            return 0, 0, 0, 0
        sc = 1.7 - 0.7 * ease_out_back(p)
        al = clamp((t - delay) / 0.08)
        if pulse and p >= 1:
            sc = 1 + 0.035 * math.sin(2 * math.pi * 1.1 * (t - delay - dur))
        dy = 0
        if exit_at is not None and t > exit_at:
            q = clamp((t - exit_at) / 0.18)
            dy -= 40 * q
            al *= 1 - q
        return 0, dy, sc, al
    return f


def anim_pop(delay, dur=0.6, pulse=0.0, exit_at=None, elastic=True):
    def f(t):
        p = clamp((t - delay) / dur)
        if p <= 0:
            return 0, 0, 0, 0
        sc = ease_out_elastic(p) if elastic else ease_out_back(p)
        if pulse and p >= 1:
            sc = 1 + pulse * math.sin(2 * math.pi * 1.4 * (t - delay - dur))
        al = clamp((t - delay) / 0.1)
        return 0, 0, sc, al
    return f


# ======================================================================
# キラキラ（強調ワードが着地した瞬間に飛ぶ星）
# ======================================================================
class Sparkle:
    def __init__(self, cx, cy, w, h, t0, seed):
        rng = np.random.default_rng(seed)
        self.t0 = t0
        self.parts = []
        for _ in range(9):
            ang = rng.uniform(0, 2 * math.pi)
            r0x, r0y = w / 2 + 10, h / 2 + 10
            sx = cx + math.cos(ang) * r0x
            sy = cy + math.sin(ang) * r0y
            self.parts.append((sx, sy, math.cos(ang), math.sin(ang), rng.uniform(20, 40), rng.random() < 0.5))

    def draw(self, d, t):
        p = (t - self.t0) / 0.55
        if p <= 0 or p >= 1:
            return
        for sx, sy, vx, vy, size, white in self.parts:
            dist = 90 * ease_out_cubic(p)
            x, y = sx + vx * dist, sy + vy * dist
            s = size * math.sin(math.pi * p)
            col = WHITE if white else BLACK
            pts = [(x, y - s), (x + s * 0.28, y - s * 0.28), (x + s, y), (x + s * 0.28, y + s * 0.28),
                   (x, y + s), (x - s * 0.28, y + s * 0.28), (x - s, y), (x - s * 0.28, y - s * 0.28)]
            d.polygon(pts, fill=col)


# ======================================================================
# テロップの組み立て
# ======================================================================
NUM_RE = re.compile(r"\d+")


def text_sprite(s, f, size, lh, accent, pad, box_w=None):
    dd = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    sw = dd.textlength(s, font=f)
    if accent:
        bw = int(box_w or sw + 2 * pad)
        sp = Image.new("RGBA", (bw, lh), (0, 0, 0, 0))
        d = ImageDraw.Draw(sp)
        top = int(size * 0.12)
        d.rounded_rectangle((0, top, bw - 1, top + int(size * 1.28)), 18, fill=BLACK)
        d.text(((bw - sw) / 2, 0), s, font=f, fill=YELLOW)
    else:
        sp = Image.new("RGBA", (int(sw) + 6, lh), (0, 0, 0, 0))
        d = ImageDraw.Draw(sp)
        d.text((3, 0), s, font=f, fill=BLACK)
    return sp


def build_telop(telop, t0=0.1, size=None, area=(420, SAFE_BOTTOM), exit_at=None, count_up=True, seed=0):
    """テロップを部品に分解して、1行ずつ・強調ワードは後からドン！で出す"""
    lines = telop.split("\n")
    size = fit_size(lines, SAFE_RIGHT - SAFE_LEFT + 20, size or 100)
    f = font(FONT_BOLD, size)
    lh = int(size * 1.55)
    y = (area[0] + area[1]) // 2 - lh * len(lines) // 2
    pad = 20
    dd = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    items, sparkles = [], []
    for li, line in enumerate(lines):
        parts = parse_line(line)
        lw = sum(dd.textlength(s, font=f) + (2 * pad if a else 0) for s, a in parts)
        x = (W - lw) / 2
        delay = t0 + li * 0.16
        for s, accent in parts:
            sw = dd.textlength(s, font=f)
            if accent:
                bw = sw + 2 * pad
                sp = text_sprite(s, f, size, lh, True, pad)
                ad = delay + 0.14
                redraw = None
                m = NUM_RE.search(s)
                if count_up and m and int(m.group()) >= 2:
                    target = int(m.group())
                    cache = {}

                    def redraw(t, s=s, m=m, target=target, ad=ad, bw=bw, cache=cache):
                        p = clamp((t - ad) / 0.75)
                        v = round(target * ease_out_cubic(p))
                        if v not in cache:
                            cache[v] = text_sprite(s[:m.start()] + str(v) + s[m.end():], f, size, lh, True, pad, bw)
                        return cache[v]
                items.append(Item(sp, x, y, anim_stamp(ad, exit_at=exit_at), redraw))
                sparkles.append(Sparkle(x + bw / 2, y + lh / 2, bw, size * 1.2, ad + 0.12, seed + li * 7 + int(x)))
                x += bw
            else:
                sp = text_sprite(s, f, size, lh, False, pad)
                items.append(Item(sp, x - 3, y, anim_slide_in(delay, exit_at=exit_at)))
                x += sw
        y += lh
    return items, sparkles


# ======================================================================
# シーン
# ======================================================================
class Scene:
    def __init__(self, sc, idx, total_scenes, ep, dur):
        self.dur = dur
        self.idx = idx
        self.total = total_scenes
        self.is_cta = "cta" in sc
        exit_at = None if self.is_cta else dur - 0.2
        self.items, self.sparkles = [], []
        self.static = []  # (sprite, x, y)
        if self.is_cta:
            self.build_cta(sc["cta"])
        else:
            self.static.append((logo_sprite(250), SAFE_LEFT, 165))
            tag = ep.get("tag", "")
            if tag:
                sp = tag_sprite(tag)
                x = W - SAFE_LEFT - sp.width
                if idx == 0:
                    self.items.append(Item(sp, x, 214, lambda t: (max(0, 1 - ease_out_cubic(clamp(t / 0.4))) * 300, 0, 1, 1)))
                else:
                    self.static.append((sp, x, 214))
            items, sparkles = build_telop(sc["telop"], size=sc.get("size"), exit_at=exit_at, seed=idx * 31)
            self.items += items
            self.sparkles += sparkles

    def build_cta(self, cta):
        info = CONFIG["cta"][cta]
        lg = logo_sprite(600)
        top = 230
        self.items.append(Item(lg, (W - lg.width) / 2, top, anim_pop(0.0, 0.8)))
        bottom = top + lg.height
        items, sparkles = build_telop(info["telop"], t0=0.45, size=74, area=(bottom + 10, bottom + 380), seed=99)
        self.items += items
        self.sparkles += sparkles
        dd = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        # ボタン（ポンと出て、ずっと脈打つ）
        fb = font(FONT_BOLD, 56)
        label = info["button"]
        bw = int(dd.textlength(label, font=fb) + 140)
        btn = Image.new("RGBA", (bw, 124), (0, 0, 0, 0))
        d = ImageDraw.Draw(btn)
        d.rounded_rectangle((0, 0, bw - 1, 123), 62, fill=BLACK)
        d.text((70, 22), label, font=fb, fill=YELLOW)
        by = bottom + 420
        self.items.append(Item(btn, (W - bw) / 2, by, anim_pop(1.1, 0.55, pulse=0.045, elastic=False)))
        # URL
        url = info["url"]
        fu_size = 40
        while fu_size > 24 and dd.textlength(url, font=font(FONT_REG, fu_size)) > W - 2 * 70:
            fu_size -= 2
        fu = font(FONT_REG, fu_size)
        ub = Image.new("RGBA", (W - 80, 120), (0, 0, 0, 0))
        d = ImageDraw.Draw(ub)
        d.rounded_rectangle((0, 0, W - 81, 119), 24, fill=WHITE, outline=BLACK, width=6)
        uw = dd.textlength(url, font=fu)
        d.text(((W - 80 - uw) / 2, 60 - fu_size * 0.62), url, font=fu, fill=BLACK)
        uy = by + 170
        self.items.append(Item(ub, 40, uy, anim_slide_in(1.35, dist=120, dur=0.45)))
        fs = font(FONT_REG, 36)
        note = "▲ プロフィールのリンクからもアクセスできます"
        nw = int(dd.textlength(note, font=fs))
        ns = Image.new("RGBA", (nw + 4, 56), (0, 0, 0, 0))
        ImageDraw.Draw(ns).text((2, 0), note, font=fs, fill=BLACK)

        def bob(t):
            dx, dy, sc, al = anim_slide_in(1.6, dist=40)(t)
            if t > 2.0:
                dy += -8 * abs(math.sin(2 * math.pi * 0.9 * (t - 2.0)))
            return dx, dy, sc, al
        self.items.append(Item(ns, (W - nw) / 2, uy + 150, bob))

    def render(self, frame, t):
        d = ImageDraw.Draw(frame)
        # 進行バー（前のシーンの位置から伸びる）
        bar_w = W - 2 * SAFE_LEFT
        p0, p1 = self.idx / self.total, (self.idx + 1) / self.total
        p = p0 + (p1 - p0) * ease_out_cubic(clamp(t / 0.5))
        d.rounded_rectangle((SAFE_LEFT, 120, SAFE_LEFT + bar_w, 134), 7, fill=(214, 190, 0))
        d.rounded_rectangle((SAFE_LEFT, 120, SAFE_LEFT + int(bar_w * p), 134), 7, fill=BLACK)
        for sp, x, y in self.static:
            frame.alpha_composite(sp, (int(x), int(y)))
        for s in self.sparkles:
            s.draw(d, t)
        for it in self.items:
            it.draw(frame, t)


# ======================================================================
# 音声
# ======================================================================
def tts(text, out_wav):
    subprocess.run(
        ["open_jtalk", "-x", DIC, "-m", VOICE, "-r", str(CONFIG.get("speech_rate", 1.1)),
         "-fm", str(CONFIG.get("pitch_shift", 0)), "-ow", str(out_wav)],
        input=text.encode("utf-8"), check=True)


def read_wav(path):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1).astype("<i2")
    return sr, data


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# ======================================================================
# 書き出し
# ======================================================================
def render_episode(ep_path):
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    scenes = ep["scenes"] + [{"cta": ep["cta"], "narration": CONFIG["cta"][ep["cta"]]["narration"]}]
    OUTPUT.mkdir(exist_ok=True)
    out = OUTPUT / f"{ep_path.stem}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1) ナレーションを作って、シーンの長さ（フレーム単位）を決める
        voice, frames, sr = [], [], None
        for i, sc in enumerate(scenes):
            wav = tmp / f"s{i:02d}.wav"
            tts(sc.get("yomi", sc["narration"]), wav)
            sr, data = read_wav(wav)
            lead = 0.15  # テロップが出てから話し始める
            dur = lead + len(data) / sr + 0.4
            if "cta" in sc:
                dur = max(dur + 1.5, 5.0)
            nf = math.ceil(dur * FPS)
            seg = np.zeros(int(nf / FPS * sr), dtype="<i2")
            st = int(lead * sr)
            seg[st:st + len(data)] = data[: len(seg) - st]
            voice.append(seg)
            frames.append(nf)
        narration = tmp / "narration.wav"
        with wave.open(str(narration), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.concatenate(voice).tobytes())
        starts = [sum(frames[:i]) / FPS for i in range(len(frames))]
        total = sum(frames) / FPS

        # 2) BGM（シーン切り替え・CTA に合わせて展開する）
        b = {**CONFIG["bgm_default"], **ep.get("bgm", {})}
        bgm_wav = tmp / "bgm.wav"
        bgm.write_wav(bgm_wav, bgm.make_track(total, b["style"], b["bpm"], b["key"], b["prog"],
                                              b.get("seed", 0), cues=starts[1:], cta_at=starts[-1]))
        vol = CONFIG.get("bgm_volume", 0.22)

        # 3) 映像をフレームごとに描いて ffmpeg に流し込む
        proc = subprocess.Popen([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-i", str(narration), "-i", str(bgm_wav),
            "-filter_complex",
            f"[1:a]aresample=44100,asplit=2[n][sc];[2:a]volume={vol}[b];"
            "[b][sc]sidechaincompress=threshold=0.02:ratio=5:attack=20:release=400[bd];"
            "[n]volume=1.6[nv];[nv][bd]amix=inputs=2:duration=first:normalize=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out)],
            stdin=subprocess.PIPE)
        bg = background_wide().convert("RGBA")
        dots = dots_overlay()
        gframe = 0
        cover = None
        for i, sc in enumerate(scenes):
            scene = Scene(sc, i, len(scenes), ep, frames[i] / FPS)
            for fi in range(frames[i]):
                t = fi / FPS
                off = int((gframe * 1.5) % STRIPE)  # ストライプがゆっくり流れる
                frame = bg.crop((off, 0, off + W, H))
                frame.alpha_composite(dots)
                scene.render(frame, t)
                proc.stdin.write(frame.convert("RGB").tobytes())
                gframe += 1
            if i == 0:
                # カバー画像：1シーン目の全部出そろった状態
                cv = bg.crop((0, 0, W, H))
                cv.alpha_composite(dots)
                scene.render(cv, min(2.0, frames[0] / FPS - 0.3))
                cover = cv.convert("RGB")
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")
        cover.save(OUTPUT / f"{ep_path.stem}_cover.png")
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
