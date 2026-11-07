"""cuts.json + narration.json + dx.wav → 1:1 편집본.

사용: python scripts/render_edit.py <회차 폴더>
출력(회차 폴더):
- edit.mp4       1080x1080, 오디오 = 원본 대사(dx)만 (나레이션 구간은 무음)
- narration.wav  편집본 시간축에 맞춘 나레이션 트랙
- preview.mp4    edit + 나레이션 섞은 확인용
- timeline.json  블록·샷·나레이션의 편집본 시간

무음: 음성(나레이션·원본 대사) 뒤 무음은 최대 3프레임 — TTS 앞뒤 무음을 자르고, 원본 대사 블록 끝도
마지막 음성 + 2프레임으로 맞춤(다음 블록 앞 1프레임과 합쳐 3프레임).
원본 컷: 원본 소스의 장면 전환(1프레임 잔상·플래시 전환 포함)을 검출해, 샷이 원본 컷을 넘어
10프레임 이하 조각이 끼지 않게 샷 위치를 옮김.
"""
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

FPS = 30
FS = 48000
OUT = 1080
NARR_LEAD = 1 / FPS   # 블록 시작 → 나레이션 음성 시작
VOICE_TAIL = 2 / FPS  # 음성 끝 → 블록 끝 (다음 블록 LEAD와 합쳐 3프레임)
REACT_TAIL = 3 / FPS  # 핵심 리액션 블록(cuts.json "react": true)은 꼬리를 더 남김 — 공감할 틈
VOICE_DB = -40        # 이보다 크면 음성(10ms RMS)
MIN_SHOT = 0.6
FADE = 0.010
CUT_DIFF = 22         # 원본 장면 전환 판정(64x64 흑백 프레임 차 평균)
EDGE = 0.5            # 샷 끝에서 이 안쪽의 원본 컷은 피함
DIALOGUE_LUFS = -18.5  # 원본 대사 블록마다 이 크기로 맞춤(나레이션과 같은 크기 — 블록마다 원음 크기가 7dB까지 다름)
PEAK_DB = -1.0
MIN_FRAG = 17 / FPS   # 말 중에 원본 컷이 있어 조각을 남길 때 최소 길이(15프레임 이하 금지)


def snap(t):
    return round(t * FPS) / FPS


def read_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(FS),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def write_wav(path, a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(FS), "-ac", "2",
                    "-i", "-", "-c:a", "pcm_s24le", path], input=a.astype(np.float32).tobytes(),
                   check=True)


def voice_bounds(a, t0=0.0):
    """오디오 a(초 t0부터)에서 음성 첫·끝 시각(초). 음성 없으면 None."""
    w = int(0.01 * FS)
    n = len(a) // w
    if n == 0:
        return None
    rms = np.sqrt((a[:n * w] ** 2).mean(1).reshape(n, w).mean(1))
    on = np.flatnonzero(20 * np.log10(rms + 1e-9) > VOICE_DB)
    if not len(on):
        return None
    return t0 + on[0] * 0.01, t0 + (on[-1] + 1) * 0.01


def source_cuts(src, rect, a, b):
    """원본 [a, b] 구간의 장면 전환 구간 [(첫 프레임, 끝 프레임 다음)] (초). rect=None이면 화면 전체."""
    a = max(0.0, math.floor(a * FPS) / FPS)  # 프레임 격자에 맞춰야 창 위치에 따라 1프레임씩 어긋나지 않음
    vf = (f"crop={rect[2]}:{rect[3]}:{rect[0]}:{rect[1]}," if rect else "") + \
        f"scale=64:64,format=gray,fps={FPS}"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}",
                          "-vf", vf, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 64 * 64).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(1)
    zones, i = [], 0
    low = 10  # 후보 찾기 문턱(밝기가 비슷한 두 장면 사이 컷은 차가 20 안팎)
    while i < len(d):
        if d[i] <= low:
            i += 1; continue
        j = i
        while j + 1 < len(d) and d[j + 1] > low:
            j += 1
        around = np.concatenate([d[max(0, i - 6):i], d[j + 1:j + 7]])
        calm = np.median(around) if len(around) else 0.0
        peak = d[i:j + 1].max()
        # 컷(1~2프레임 튐)·플래시 전환(12프레임 이하 연속)만. 카메라 이동처럼 앞뒤도 계속 큰 변화는 제외
        hard = j - i < 2 and (peak > CUT_DIFF and peak > 1.8 * calm or peak > 6 * (calm + 1))
        flash = 2 <= j - i < 12 and peak > 35 and peak > 1.8 * calm
        if hard or flash:
            zones.append((a + (i + 1) / FPS, a + (j + 2) / FPS))
        i = j + 1
    return zones


def scene_of(zones, a, b):
    """[a, b]와 가장 많이 겹치는, 원본 컷 사이 장면 구간."""
    edges = [(-1e9, -1e9)] + zones + [(1e9, 1e9)]
    best, scene = -1, (a, b)
    for (_, s0), (s1, _) in zip(edges, edges[1:]):
        ov = min(b, s1) - max(a, s0)
        if ov > best:
            best, scene = ov, (s0, s1)
    return scene


def place_shots(shots, src, rect):
    """나레이션 블록 샷: 원본 컷을 넘지 않게 장면 안으로 밀고, 장면이 짧으면 모자란 길이를 다음 샷에 넘김."""
    out, carry = [], 0.0
    for k, s in enumerate(shots):
        a, b = s["src"]
        L = b - a + carry
        zones = source_cuts(src, rect, a - L - 1, b + L + 1)
        s0, s1 = scene_of(zones, a, a + L)
        s0, s1 = s0 + 1 / FPS, s1 - 1 / FPS
        if s1 - s0 >= L:
            a = min(max(a, s0), s1 - L)
            out.append({**s, "src": [a, a + L]}); carry = 0.0
        elif s1 - s0 >= MIN_SHOT or k == len(shots) - 1:
            out.append({**s, "src": [s0, s1]}); carry = L - (s1 - s0)
        else:
            carry = L
    if carry > 1e-3 and out:  # 마지막에 남은 길이는 앞쪽 샷들 시작을 당겨 채움
        for s in reversed(out):
            a, b = s["src"]
            s0, _ = scene_of(source_cuts(src, rect, a - carry - 1, b), a, b)
            take = min(carry, a - (s0 + 1 / FPS))
            if take > 0:
                s["src"] = [a - take, b]; carry -= take
            if carry <= 1e-3:
                break
    return out


def trim_pic(shots, dx, keep_end=None, tail=VOICE_TAIL):
    """화면을 다른 시각에서 가져오는 블록(샷에 "pic"): 소리(src)는 이어진 그대로 두고 블록 앞뒤 무음만 음성 기준으로 줄임.
    샷 경계는 손대지 않음(원본 컷 검사는 기획 때 pic 기준으로 끝냄). 화면 시작 = src 시작 + pic_off.
    말하는 사람이 나오는 샷은 pic == src(입모양 싱크)."""
    shots = [{**s, "src": list(s["src"]), "pic_off": (s["pic"][0] - s["src"][0]) if "pic" in s else 0.0} for s in shots]
    a, b = shots[0]["src"][0], shots[-1]["src"][1]
    vb = voice_bounds(dx[int(a * FS):int(b * FS)], a)
    if vb:
        vend = max(vb[1], keep_end or 0)
        a, b = max(a, vb[0] - 1 / FPS), min(b + tail, vend + tail)
    out = []
    for s in shots:
        x0, x1 = max(s["src"][0], a), s["src"][1]
        if s is shots[-1]:
            x1 = b
        else:
            x1 = min(x1, b)
        if x1 - x0 >= 1 / FPS:
            out.append({**s, "src": [float(x0), float(x1)], "pic_off": float(s["pic_off"])})
    # 앞 무음을 자르다 첫 샷이 0.4초 미만이 되면 다음 샷이 그 시간을 덮음(같은 pic_off로 화면 시작을 당김)
    if len(out) > 1 and out[0]["src"][1] - out[0]["src"][0] < 0.4:
        out[1]["src"][0] = out[0]["src"][0]
        out.pop(0)
    return out


def trim_orig(shots, src, rect, dx, keep_end=None, tail=VOICE_TAIL):
    """원본 대사 블록: 앞뒤 무음을 음성 기준으로 줄이고, 원본 컷 기준으로 샷을 다시 나눔.

    - 끝은 마지막 음성(+ 자막 계획의 마지막 대사 끝 keep_end) + 2프레임
    - 블록 끝·처음에 EDGE보다 짧은 조각이 생기면: 말이 없으면 잘라내고, 말 중이면 조각을 EDGE까지 늘림
    - 샷은 원본 컷마다 나눔. 반전·확대는 그 시간을 덮던 원래 샷 설정을 따름
    """
    a, b = shots[0]["src"][0], shots[-1]["src"][1]
    vb = voice_bounds(dx[int(a * FS):int(b * FS)], a)
    if vb:
        vend = max(vb[1], keep_end or 0)
        a, b = max(a, vb[0] - 1 / FPS), min(b + tail, vend + tail)
    zones = source_cuts(src, rect, a - EDGE - 1, b + EDGE + 1)

    def free(x0, x1):
        return not any(z[1] > x0 and z[0] < x1 for z in zones)

    inside = [z for z in zones if z[1] > a and z[0] < b]
    if inside and inside[0][0] - a < EDGE:
        z0, z1 = inside[0]
        if not vb or z1 <= vb[0] + 0.05 or z1 - a <= 0.3:
            a = z1
        elif free(z0 - MIN_FRAG, z0):
            a = z0 - MIN_FRAG
    inside = [z for z in zones if z[1] > a and z[0] < b]
    if inside and b - inside[-1][1] < EDGE:
        z0, z1 = inside[-1]
        if not vb or z0 >= vend - 0.05:
            b = z0 - 2 / FPS  # 검출 구간이 실제 컷보다 1프레임 늦어, z0까지 쓰면 마지막 프레임에 다음 장면이 섞임
        elif free(z1, z1 + MIN_FRAG):
            b = z1 + MIN_FRAG
    # 원본 컷 앞뒤 1프레임씩 여유(경계에서 다른 카메라 1프레임이 섞이는 것 방지)
    edges = [a] + [x for z in zones if a < z[0] < b for x in (z[0] - 1 / FPS, z[1] + 1 / FPS)] + [b]
    out = []
    for x0, x1 in zip(edges[::2], edges[1::2]):
        if x1 - x0 < 1 / FPS:
            continue
        mid = (x0 + x1) / 2
        base = min(shots, key=lambda s: 0 if s["src"][0] <= mid <= s["src"][1]
                   else min(abs(mid - s["src"][0]), abs(mid - s["src"][1])))
        out.append({**base, "src": [x0, x1]})
    for sh in out:  # 마지막 확인: 샷 가장자리 0.2초 안의 원본 컷은 잘라냄(잔상 1~2프레임 방지)
        x0, x1 = sh["src"]
        for z0, z1 in zones:
            if x0 < z0 < x1 and x1 - z0 < 0.2:
                x1 = z0 - 1 / FPS
            if x0 < z1 < x1 and z1 - x0 < 0.2:
                x0 = z1 + 1 / FPS
        sh["src"] = [x0, x1]
    return [sh for sh in out if sh["src"][1] - sh["src"][0] > 1 / FPS]


def fit_shots(shots, target):
    """샷 길이 합을 target에 맞춤: 마지막 샷을 늘이거나 줄이고, 너무 짧아지면 앞 샷에 흡수."""
    shots = [dict(s) for s in shots]
    while True:
        total = sum(s["src"][1] - s["src"][0] for s in shots)
        last = shots[-1]
        new_len = last["src"][1] - last["src"][0] + (target - total)
        if new_len >= MIN_SHOT or len(shots) == 1:
            last["src"] = [last["src"][0], last["src"][0] + new_len]
            return shots
        shots.pop()


def crop_filter(rect, s):
    x0, y0, w, h = rect
    side = round(h / s["zoom"] / 2) * 2
    x = x0 + s["cx"] * w - side / 2
    x = int(min(max(x, x0), x0 + w - side))
    # 확대 시 세로 위치: 기본 0.35(얼굴·제품 쪽), 하단 방송 그래픽을 피할 땐 cuts.json "cy": 0
    y = int(y0 + (h - side) * s.get("cy", 0.35))
    f = f"crop={side}:{side}:{x}:{y},scale={OUT}:{OUT}:flags=lanczos"
    if s["flip"]:
        f += ",hflip"
    return f + f",fps={FPS},setsar=1"


def main(ep):
    cuts = json.load(open(os.path.join(ep, "cuts.json")))
    narr = {l["i"]: l for l in json.load(open(os.path.join(ep, "narration.json")))["lines"]}
    src = os.path.join(ep, cuts["source"])
    # 대사 분리본: audio/dx.wav + 나중에 따로 분리한 audio/add*/dx.wav (기존 dx를 덮어쓰지 않음)
    # → 샷마다 그 구간을 가장 많이 덮는 분리본을 씀(같으면 dx.wav 우선)
    stem_names = ["dx.wav"] + [d + "/dx.wav" for d in sorted(os.listdir(os.path.join(ep, "audio")))
                               if d.startswith("add") and os.path.exists(os.path.join(ep, "audio", d, "dx.wav"))]
    stems = [read_audio(os.path.join(ep, "audio", nm)) for nm in stem_names]
    n_max = max(len(x) for x in stems)
    stems = [np.pad(x, ((0, n_max - len(x)), (0, 0))) for x in stems]

    W = int(0.01 * FS)  # 10ms 단위: 프레임 안에 0 아닌 샘플이 하나라도 있으면 「분리됨」(순간 무음 샘플 무시)

    def frames_on(x):
        n = len(x) // W
        f = np.abs(x[:n * W]).reshape(n, W, -1).max((1, 2)) > 0
        # 0.1초 미만의 빈틈은 메움(조용한 순간)
        idx = np.flatnonzero(f)
        for a, b in zip(idx, idx[1:]):
            if 1 < b - a <= 10:
                f[a:b] = True
        return f
    on = [frames_on(x) for x in stems]

    def margin(k, i0, i1):
        """구간이 분리 범위 가장자리에서 떨어진 거리(초). 분리 창 끝자락(앞뒤 3초 여유)은 품질이 나쁨."""
        m = on[k]
        i0, i1 = i0 // W, min(len(m), i1 // W + 1)
        if i1 <= i0 or not m[i0:i1].all():
            return -1.0
        left = np.flatnonzero(~m[:i0])
        right = np.flatnonzero(~m[i1:])
        a = i0 - (left[-1] + 1 if len(left) else 0)
        b = right[0] if len(right) else len(m) - i1
        return min(a, b) * 0.01

    def pick_k(a, b):
        """가장자리에서 1초 이상 떨어진 분리본 중 목소리가 가장 크게 남은 것(분리가 목소리를 잃지 않은 것).
        그런 게 없으면 가장자리에서 가장 먼 것."""
        i0, i1 = int(a * FS), int(b * FS)
        ms = [margin(k, i0, i1) for k in range(len(stems))]
        good = [k for k in range(len(stems)) if ms[k] >= 1.0]
        if good:
            k = max(good, key=lambda k: loud(stems[k][i0:i1]))
            return k, ms[k]
        return int(np.argmax(ms)), max(ms)

    def pick(a, b):
        return stems[pick_k(a, b)[0]]
    dx = stems[0].copy()
    mix = read_audio(src)  # 원본 방송 소리(분리가 목소리를 잃은 샷 대체용)
    import pyloudnorm
    meter = pyloudnorm.Meter(FS)

    def loud(x):
        """LUFS. 0.4초 미만은 RMS로 근사."""
        if len(x) == 0 or np.abs(x).max() == 0:
            return -120.0
        if len(x) >= int(0.45 * FS):
            v = meter.integrated_loudness(x.astype(np.float64))
            if np.isfinite(v):
                return v
        return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12) - 0.7
    plan_p = os.path.join(ep, "subs_plan.json")
    plan_ends = [o["src"][1] for o in json.load(open(plan_p))["orig"]] if os.path.exists(plan_p) else []

    # 1) 타임라인
    t, blocks = 0.0, []
    for b in cuts["blocks"]:
        shots = b["shots"]
        if b["kind"] == "narr":
            line = narr[b["line"]]
            von, voff = voice_bounds(read_audio(line["file"]))
            shots = fit_shots(shots, NARR_LEAD + (voff - von) + VOICE_TAIL)
            need = NARR_LEAD + (voff - von) + VOICE_TAIL
            shots = place_shots(shots, src, cuts["content_rect"])
            have = sum(sh["src"][1] - sh["src"][0] for sh in shots)
            if have < need - 1 / FPS:  # 화면 장면이 모자라 나레이션이 잘림 → cuts.json에 화면을 더 넣어야 함
                print(f"⚠ 나레이션 {b['line']}번 화면이 {need - have:.2f}초 모자람(나레이션 끝이 잘림) — 화면 샷 추가 필요")
        else:
            s0, s1 = shots[0]["src"][0], shots[-1]["src"][1]
            keep = max([e for e in plan_ends if s0 < e <= s1 + 0.2], default=None)
            if b.get("keep_end"):  # 반응컷이 붙은 블록: 마지막 반응컷 끝까지 살림
                keep = max(keep or 0, b["keep_end"] - VOICE_TAIL)
            if any("pic" in x for x in shots):
                shots = trim_pic(shots, pick(s0, s1), keep, VOICE_TAIL + (REACT_TAIL if b.get("react") else 0))
            else:
                # 샷 사이에 일부러 비운 구간(대사 안 무음 잘라냄)이 있으면 이어진 묶음마다 따로 다듬음
                groups = [[shots[0]]]
                for x in shots[1:]:
                    if x["src"][0] - groups[-1][-1]["src"][1] > 0.05:
                        groups.append([x])
                    else:
                        groups[-1].append(x)
                tail = VOICE_TAIL + (REACT_TAIL if b.get("react") else 0)
                shots = []
                for gi, g in enumerate(groups):
                    last = gi == len(groups) - 1
                    shots += trim_orig(g, src, cuts["content_rect"], pick(s0, s1), keep if last else None,
                                       tail if last else VOICE_TAIL)
        start = t
        out_shots = []
        for s in shots:
            dur = snap(s["src"][1] - s["src"][0])
            out_shots.append({**s, "src": [round(s["src"][0], 3), round(s["src"][0] + dur, 3)],
                              "start": round(t, 3), "end": round(t + dur, 3)})
            t += dur
        blk = {**b, "shots": out_shots, "start": round(start, 3), "end": round(t, 3)}
        if b["kind"] == "narr":
            # narr_start = 나레이션 파일 0초의 편집본 시각(앞 무음만큼 당김 → 음성이 LEAD에 시작)
            blk["narr_start"] = round(start + NARR_LEAD - von, 3)
            blk["narr_text"] = narr[b["line"]]["text"]
        blocks.append(blk)
    total = t

    # 2) 영상: 샷별 렌더 → concat
    tmp = tempfile.mkdtemp(prefix="shorts_render_")
    parts = []
    for bi, b in enumerate(blocks):
        for si, s in enumerate(b["shots"]):
            p = os.path.join(tmp, f"{bi:02d}_{si}.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s['src'][0] + s.get('pic_off', 0.0):.3f}", "-i", src,
                            "-frames:v", str(round((s["end"] - s["start"]) * FPS)), "-vf",
                            crop_filter(cuts["content_rect"], s), "-an", "-c:v", "libx264",
                            "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", p],
                           check=True)
            parts.append(p)
    lst = os.path.join(tmp, "list.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    video = os.path.join(tmp, "video.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", video], check=True)

    # 3) 오디오: 원본 대사 블록은 dx, 나레이션은 별도 트랙
    n = int(round(total * FS))
    dx_track = np.zeros((n, 2), np.float32)
    nr_track = np.zeros((n, 2), np.float32)
    fl = int(FADE * FS)
    ramp = np.linspace(0, 1, fl, dtype=np.float32)[:, None]
    for b in blocks:
        if b["kind"] == "orig":
            # 블록 전체를 한 분리본으로(샷마다 바꿔 끼우면 목소리 질감·크기가 튐). 블록을 다 덮는 게 없을 때만 샷마다
            bk, bm = pick_k(min(x["src"][0] for x in b["shots"]), max(x["src"][1] for x in b["shots"]))
            # block_gain: 블록 전체에 한 음량(샷마다 맞추면 한 사람 말 중간에 5dB씩 튀어 목소리가 이상하게 들림)
            # orig_audio: 분리본 대신 원음(삐 처리 욕설·관중 반응처럼 분리가 소리를 지우는 샷)
            bgain = None
            if any(x.get("block_gain") for x in b["shots"]):
                cat = []
                for s in b["shots"]:
                    a0, a1 = int(s["src"][0] * FS), int(s["src"][1] * FS)
                    k = bk if bm >= 0 else pick_k(s["src"][0], s["src"][1])[0]
                    cat.append((mix if s.get("orig_audio") else stems[k])[a0:a1])
                cat = np.concatenate(cat)
                lv = loud(cat)
                if lv > -60:
                    bgain = 10 ** (np.clip(DIALOGUE_LUFS - lv, -6, 12) / 20)
                    pk = np.abs(cat).max() * bgain
                    if pk > 10 ** (PEAK_DB / 20):
                        bgain *= 10 ** (PEAK_DB / 20) / pk
            for s in b["shots"]:
                k, m = (bk, bm) if bm >= 0 else pick_k(s["src"][0], s["src"][1])
                s["stem"], s["stem_margin"] = stem_names[k], round(m, 2)
                if s.get("mute"):  # 반응컷 아래로 다음 말이 반쯤 들어가는 경우 대사 소리 끔
                    continue
                a0, a1 = int(s["src"][0] * FS), int(s["src"][1] * FS)
                seg = stems[k][a0:a1].copy()
                # 분리가 목소리를 잃은 샷(멀리서 한 말 등): 분리본이 원음보다 10dB 넘게 작으면 그 샷만 원음
                ld, lm = loud(seg), loud(mix[a0:a1])
                if s.get("orig_audio"):
                    seg = mix[a0:a1].copy()
                    s["stem"] = "원음(지정)"
                elif lm > -50 and ld < lm - 10:
                    seg = mix[a0:a1].copy()
                    s["stem"] = "원음(분리 손실)"
                # 샷마다 음량을 DIALOGUE_LUFS로(+12/−6dB 안), 피크는 PEAK_DB 이하
                lv = loud(seg)
                if bgain is not None:
                    seg *= bgain
                    s["gain_db"] = round(20 * np.log10(bgain), 1)
                elif lv > -60:
                    g = 10 ** (np.clip(DIALOGUE_LUFS - lv, -6, 12) / 20)
                    pk = np.abs(seg).max() * g
                    if pk > 10 ** (PEAK_DB / 20):
                        g *= 10 ** (PEAK_DB / 20) / pk
                    seg *= g
                    s["gain_db"] = round(20 * np.log10(g), 1)
                o0 = int(s["start"] * FS)
                seg = seg[: n - o0]
                dx_track[o0:o0 + len(seg)] += seg
            b0, b1 = int(b["start"] * FS), min(n, int(b["end"] * FS))  # 반올림으로 끝이 n을 넘지 않게
            # 블록 경계에만 페이드(블록 안 샷 경계는 연속 음성)
            dx_track[b0:b0 + fl] *= ramp
            dx_track[b1 - fl:b1] *= ramp[::-1]
        else:
            a = read_audio(narr[b["line"]]["file"])
            o0 = int(b["narr_start"] * FS)
            if o0 < 0:
                a, o0 = a[-o0:], 0
            a = a[: min(n, int(b["end"] * FS)) - o0]  # 블록 밖 꼬리 무음은 자름
            nr_track[o0:o0 + len(a)] += a
    dx_wav = os.path.join(tmp, "dx_track.wav")
    write_wav(dx_wav, dx_track)
    write_wav(os.path.join(ep, "narration.wav"), nr_track)

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", dx_wav, "-map", "0:v",
                    "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                    os.path.join(ep, "edit.mp4")], check=True)
    mix = os.path.join(tmp, "mix.wav")
    write_wav(mix, dx_track + nr_track)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", mix, "-map", "0:v",
                    "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                    os.path.join(ep, "preview.mp4")], check=True)

    json.dump({"duration": round(total, 3), "fps": FPS, "blocks": blocks},
              open(os.path.join(ep, "timeline.json"), "w"), ensure_ascii=False, indent=1, default=float)
    n_shots = sum(len(b["shots"]) for b in blocks)
    lens = sorted(s["end"] - s["start"] for b in blocks for s in b["shots"])
    print(f"길이 {total:.2f}초, 샷 {n_shots}개, 샷 길이 중앙값 {lens[len(lens) // 2]:.2f}초, "
          f"최단 {lens[0]:.2f}초, 반전 {sum(s['flip'] for b in blocks for s in b['shots'])}개")


if __name__ == "__main__":
    main(sys.argv[1])
