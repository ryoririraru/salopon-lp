#!/usr/bin/env python3
"""サロポンリール用オリジナルBGMシンセサイザー

外部音源を使わず numpy で一から合成するので、著作権・利用料の心配がない。
テンポの良いアップビートなポップ／ファンク系トラックを、動画の長さとシーン切り替えに合わせて生成する。

  python3 bgm.py out.wav 32.5 --style pop --bpm 124 --key C
"""
import argparse
import wave

import numpy as np
from scipy.signal import butter, lfilter

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
# 長調のディグリー → 半音（I ii iii IV V vi）
DEG = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9}
MINOR_DEG = {2, 3, 6}
PROGS = {
    "pop":    [1, 5, 6, 4],   # 王道の明るい進行
    "lift":   [4, 5, 3, 6],   # 王道進行（J-POP的な高揚感）
    "bounce": [1, 6, 4, 5],
    "hope":   [6, 4, 1, 5],
}


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def lp(x, cutoff, order=2):
    b, a = butter(order, min(cutoff / (SR / 2), 0.99), "low")
    return lfilter(b, a, x)


def hp(x, cutoff, order=2):
    b, a = butter(order, cutoff / (SR / 2), "high")
    return lfilter(b, a, x)


def bp(x, lo, hi):
    b, a = butter(2, [lo / (SR / 2), hi / (SR / 2)], "band")
    return lfilter(b, a, x)


def env(n, a=0.005, d=0.1, s=0.0, r=0.05, hold=None):
    """シンプルな ADSR（hold=サステイン長サンプル）"""
    t = np.arange(n) / SR
    e = np.ones(n) * s
    ai = int(a * SR)
    e[:ai] = np.linspace(0, 1, ai, endpoint=False) if ai else e[:ai]
    dec = (t >= a)
    e[dec] = s + (1 - s) * np.exp(-(t[dec] - a) / max(d, 1e-4))
    if hold is not None and hold < n:
        rel = np.arange(n - hold) / SR
        e[hold:] = e[hold] * np.exp(-rel / max(r, 1e-4))
    return e


def saw(freq, n, detune=0.0, rng=None):
    t = np.arange(n) / SR
    ph = (rng.random() if rng is not None else 0)
    f = freq * (1 + detune)
    return 2 * ((t * f + ph) % 1.0) - 1


def square(freq, n, duty=0.5):
    t = np.arange(n) / SR
    return np.where((t * freq) % 1.0 < duty, 1.0, -1.0)


# ---------- 楽器 ----------
def kick(n=int(0.35 * SR)):
    t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 30)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 9)
    click = np.random.default_rng(1).standard_normal(n) * np.exp(-t * 400) * 0.3
    return np.tanh((body + click) * 1.6)


def snare(rng, n=int(0.25 * SR)):
    t = np.arange(n) / SR
    noise = bp(rng.standard_normal(n), 1500, 9000) * np.exp(-t * 18)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)
    return noise * 0.9 + tone * 0.5


def clap(rng, n=int(0.3 * SR)):
    t = np.arange(n) / SR
    noise = bp(rng.standard_normal(n), 900, 5000)
    e = np.zeros(n)
    for off in (0.0, 0.011, 0.022):
        i = int(off * SR)
        e[i:] += np.exp(-(t[: n - i]) * 60)
    e += np.exp(-t * 14) * 0.6
    return noise * e * 0.7


def hat(rng, open_=False):
    n = int((0.22 if open_ else 0.05) * SR)
    t = np.arange(n) / SR
    return hp(rng.standard_normal(n), 7000) * np.exp(-t * (14 if open_ else 90))


def crash(rng):
    n = int(1.8 * SR)
    t = np.arange(n) / SR
    return hp(rng.standard_normal(n), 5000) * np.exp(-t * 3.0) * 0.3


def riser(rng, dur):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    # だんだん明るくなるノイズ（区間ごとにフィルタ）
    chunks = 16
    for c in range(chunks):
        a, b = c * n // chunks, (c + 1) * n // chunks
        out[a:b] = bp(noise[a:b], 400 + 6000 * c / chunks, 600 + 9000 * c / chunks)
    return out * t ** 2 * 0.6


def whoosh(rng):
    n = int(0.35 * SR)
    t = np.linspace(0, 1, n)
    x = bp(rng.standard_normal(n), 800, 6000)
    return x * np.sin(np.pi * t) ** 2 * 0.35


def pluck_chord(notes, n, rng, bright=3500):
    x = np.zeros(n)
    for m in notes:
        for dt in (-0.006, 0.0, 0.007):
            x += saw(midi_hz(m), n, dt, rng)
    x /= len(notes) * 3
    e = env(n, a=0.003, d=0.12, s=0.0)
    # フィルターエンベロープ風に：明るい頭＋暗い尻尾
    return lp(x, bright) * e * 0.8 + lp(x, 900) * e * 0.4


def pad_chord(notes, n, rng):
    x = np.zeros(n)
    for m in notes:
        for dt in (-0.004, 0.004):
            x += saw(midi_hz(m), n, dt, rng)
    x = lp(x / (len(notes) * 2), 1800)
    t = np.arange(n) / SR
    e = np.minimum(1, t / 0.05) * np.minimum(1, (n / SR - t) / 0.08)
    return x * e * 0.25


def bass_note(m, n, style):
    f = midi_hz(m)
    x = 0.6 * square(f, n, 0.5) + 0.6 * saw(f, n)
    x = lp(x, 700 if style != "funk" else 1100)
    x += np.sin(2 * np.pi * f * np.arange(n) / SR) * 0.8  # サブ
    e = env(n, a=0.004, d=0.18 if style == "funk" else 0.35, s=0.35, r=0.03, hold=int(n * 0.85))
    return np.tanh(x * e * 1.2) * 0.7


def lead_note(m, n):
    f = midi_hz(m)
    t = np.arange(n) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(1, t / 0.15)
    ph = 2 * np.pi * np.cumsum(f * vib) / SR
    x = 0.55 * np.sign(np.sin(ph)) * 0.5 + 0.5 * np.sin(ph) + 0.25 * np.sin(2 * ph)
    x = lp(x, 4200)
    return x * env(n, a=0.006, d=0.25, s=0.4, r=0.05, hold=int(n * 0.8)) * 0.35


def bell(m, n):
    f = midi_hz(m)
    t = np.arange(n) / SR
    mod = np.sin(2 * np.pi * f * 3.5 * t) * 2.0 * np.exp(-t * 6)
    return np.sin(2 * np.pi * f * t + mod) * np.exp(-t * 5) * 0.25


def add(buf, x, pos, gain=1.0):
    i = int(pos * SR)
    if i >= len(buf) or i < 0:
        return
    j = min(len(buf), i + len(x))
    buf[i:j] += x[: j - i] * gain


# ---------- 曲の組み立て ----------
def chord_notes(root_midi, degree):
    r = root_midi + DEG[degree]
    third = 3 if degree in MINOR_DEG else 4
    return r, [r + 12, r + 12 + third, r + 12 + 7, r + 24]


def make_track(duration, style="pop", bpm=124, key="C", prog="pop", seed=0, cues=(), cta_at=None):
    rng = np.random.default_rng(seed)
    total = duration + 0.5
    n = int(total * SR)
    drums = np.zeros(n)
    music = np.zeros(n)
    fx = np.zeros(n)
    beat = 60.0 / bpm
    bar = beat * 4
    root = 48 + NOTE[key]  # C3 付近
    if root > 52:
        root -= 12
    progression = PROGS[prog]
    k = kick()
    # メロディ用スケール（メジャー・ペンタトニック）
    penta = [0, 2, 4, 7, 9]
    scale = [root + 24 + o + 12 * oc for oc in range(2) for o in penta]
    motif = rng.choice(len(scale), size=8)  # 1小節8音の短いモチーフ

    intro_end = bar * 1  # 最初の1小節はキックなし（ビルド）
    t_bar = 0.0
    bar_i = 0
    while t_bar < total:
        deg = progression[bar_i % len(progression)]
        broot, notes = chord_notes(root, deg)
        in_cta = cta_at is not None and t_bar >= cta_at - 0.01
        full = t_bar >= intro_end
        for b in range(4):
            tb = t_bar + b * beat
            # --- ドラム ---
            if full:
                if style == "funk":
                    if b in (0, 2):
                        add(drums, k, tb, 0.95)
                    if b == 2:
                        add(drums, k, tb + beat * 0.75, 0.6)
                else:
                    add(drums, k, tb, 0.95)
                if b in (1, 3):
                    add(drums, snare(rng), tb, 0.45)
                    add(drums, clap(rng), tb, 0.45)
            # ハイハット（16分 or 8分）
            steps = 4 if (style == "funk" or in_cta) else 2
            for s in range(steps):
                ts = tb + s * beat / steps
                is_off = (s == steps // 2)
                if style == "bounce" and is_off and full:
                    add(drums, hat(rng, open_=True), ts, 0.18)
                else:
                    add(drums, hat(rng), ts, (0.22 if is_off else 0.12) * (1 if full else 0.7))
            # --- コード（裏拍のスタブ）---
            stab_n = int(beat * 0.45 * SR)
            if style == "funk":
                for off in (0.5, 0.75) if b % 2 else (0.5,):
                    add(music, pluck_chord(notes, stab_n, rng, 3000), tb + off * beat, 0.55)
            else:
                add(music, pluck_chord(notes, stab_n, rng), tb + 0.5 * beat, 0.6)
                if b == 0:
                    add(music, pluck_chord(notes, stab_n, rng), tb, 0.45)
            # --- ベース ---
            if full:
                bn = int(beat * 0.5 * SR)
                if style == "funk":
                    pat = [(0, broot), (0.75, broot + 12), (1.5 if b % 2 else 0.5, broot + 7)]
                    for off, m in pat:
                        if off < 1:
                            add(music, bass_note(m, int(beat * 0.3 * SR), style), tb + off * beat, 0.8)
                else:
                    add(music, bass_note(broot, bn, style), tb, 0.8)
                    add(music, bass_note(broot + (12 if b % 2 else 0), bn, style), tb + 0.5 * beat, 0.7)
        # パッド（全体の厚み）
        add(music, pad_chord(notes, int(bar * SR), rng), t_bar, 0.8)
        # リード（2小節目以降の偶数小節 & CTA ではメロディを鳴らす）
        if full and (bar_i % 2 == 1 or in_cta):
            chord_pcs = {(m - root) % 12 for m in notes}
            for i_, idx in enumerate(motif):
                m = scale[idx]
                if i_ % 2 == 0:  # 強拍はコードトーンに寄せる
                    while (m - root) % 12 not in chord_pcs:
                        m -= 1
                if rng.random() < 0.2 and i_ not in (0, 4):
                    continue
                inst = lead_note(m, int(beat * 0.48 * SR)) if not in_cta else lead_note(m, int(beat * 0.48 * SR)) * 1.2
                add(music, inst, t_bar + i_ * beat / 2, 0.9)
                add(music, bell(m + 12, int(0.6 * SR)), t_bar + i_ * beat / 2, 0.25 if in_cta else 0.12)
        t_bar += bar
        bar_i += 1

    # シーン切り替え効果音・CTA 演出
    for c in cues:
        if c > 0.3:
            add(fx, whoosh(rng), c - 0.25, 1.0)
    if cta_at:
        add(fx, riser(rng, min(bar, cta_at)), cta_at - min(bar, cta_at), 1.0)
        add(fx, crash(rng), cta_at, 1.0)
        add(drums, k, cta_at, 0.6)
    add(fx, crash(rng), intro_end, 0.6)

    # ミックス
    mix = drums * 0.9 + music * 0.75 + fx * 0.6
    # 頭と尻のフェード
    fade_in = int(0.05 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)
    fo = int(1.5 * SR)
    end = int(duration * SR)
    mix = mix[:end]
    mix[-fo:] *= np.linspace(1, 0, fo) ** 1.5
    # 簡易マスタリング
    mix = hp(mix, 30)
    mix = np.tanh(mix / np.max(np.abs(mix)) * 1.25) * 0.85
    stereo = np.stack([mix, mix], axis=1)
    # 軽いステレオ感（ハイハット等は少し左右へ）
    return stereo


def write_wav(path, stereo):
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("duration", type=float)
    ap.add_argument("--style", default="pop", choices=["pop", "funk", "bounce"])
    ap.add_argument("--bpm", type=int, default=124)
    ap.add_argument("--key", default="C")
    ap.add_argument("--prog", default="pop", choices=list(PROGS))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    write_wav(a.out, make_track(a.duration, a.style, a.bpm, a.key, a.prog, a.seed, cta_at=a.duration - 5))
