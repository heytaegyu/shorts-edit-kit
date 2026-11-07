"""자동 검수 → qa_report.md

사용: python scripts/qa.py <회차 폴더>
- 편집본 샷 재검출: 15프레임(0.5초) 이하 조각(잔상·플래시 포함), 계획에 없는 컷, 검은/흰 프레임
- 블록 경계 무음: 음성 끝 → 다음 음성 시작이 3프레임 넘는 곳(블록 사이만)
- 출력 전 소리 검사: 원본 대사 블록마다 ① 분리본이 섞였는지·분리 가장자리(품질 위험)·분리 안 된 구간
  ② 받아쓰기 ↔ 기대 대사(subs_plan 영어) 유사도 — 0.5 미만이면 「소리 망가짐 의심」
- 영상·오디오 길이, 라우드니스
- 자막: 조각 글자 수(5~12), 초당 글자 수(≤12), 온점·반점 잔존, 최소 노출
"""
import json
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_edit import FPS, read_audio, source_cuts, voice_bounds  # noqa: E402


def ko_len(s):
    """자막 글자 수(공백 제외). 원화 아랫줄(*7천만 원*)은 보조라 빼고 셈."""
    return len(s.split("\n")[0].replace(" ", ""))


def dur(path, stream):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
                          "stream=duration", "-of", "csv=p=0", path], capture_output=True,
                         text=True).stdout.strip()
    return float(out)


def frame_luma(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=64:64,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 64 * 64).mean(axis=1)


def main(ep):
    edit = os.path.join(ep, "edit.mp4")
    tl = json.load(open(os.path.join(ep, "timeline.json")))
    subs = json.load(open(os.path.join(ep, "subs.json")))["events"]
    lines, issues = [], 0

    planned = [round(s["start"] * FPS) for b in tl["blocks"] for s in b["shots"]][1:]
    found = [round(a * FPS) for a, _ in source_cuts(edit, None, 0, tl["duration"] + 1)]
    marks = [0] + sorted(set(found)) + [round(tl["duration"] * FPS)]
    short = [(a / FPS, b / FPS) for a, b in zip(marks, marks[1:]) if b - a <= 15]
    extra = sorted(set(found) - set(planned))
    luma = frame_luma(edit)
    odd = [i / tl["fps"] for i, v in enumerate(luma) if v < 12 or v > 243]
    vd, ad = dur(edit, "v:0"), dur(edit, "a:0")

    # 블록 경계 무음(나레이션 + 원본 대사를 합친 소리 기준)
    mix = read_audio(os.path.join(ep, "preview.mp4"))
    gaps = []
    for b0, b1 in zip(tl["blocks"], tl["blocks"][1:]):
        t = b1["start"]
        va = voice_bounds(mix[int(max(0, t - 1.5) * 48000):int(t * 48000)], max(0, t - 1.5))
        vb = voice_bounds(mix[int(t * 48000):int((t + 1.5) * 48000)], t)
        if va and vb and (vb[0] - va[1]) * FPS > 3.5:
            gaps.append(f"{va[1]:.2f}~{vb[0]:.2f}({(vb[0] - va[1]) * FPS:.0f}f)")

    lines += ["# 자동 검수", "", f"- 길이: 영상 {vd:.2f}초 / 오디오 {ad:.2f}초 "
              f"({'일치' if abs(vd - ad) < 0.05 else '불일치'})",
              f"- 계획 컷 {len(planned)}개 / 재검출 컷 {len(found)}개, 계획에 없는 컷 {len(extra)}개 "
              + ", ".join(f"{x / FPS:.2f}" for x in extra)]
    lines.append(f"- 15프레임 이하 조각: {len(short)}개 " +
                 ", ".join(f"{a:.2f}~{b:.2f}" for a, b in short))
    lines.append(f"- 검은/흰 프레임: {len(odd)}개 " + ", ".join(f"{t:.2f}" for t in odd[:10]))
    lines.append(f"- 블록 경계 무음 3프레임 초과: {len(gaps)}개 " + ", ".join(gaps))
    issues += len(short) + len(extra) + len(odd) + len(gaps)

    # 출력 전 소리 검사
    import difflib
    import tempfile
    plan = json.load(open(os.path.join(ep, "subs_plan.json")))
    words = lambda t: re.findall(r"[a-z0-9']+", t.lower())
    audio_bad = []
    try:
        import mlx_whisper
    except ImportError:
        mlx_whisper = None
    tmpd = tempfile.mkdtemp(prefix="shorts_qa_")
    for i, b in enumerate(tl["blocks"]):
        if b["kind"] != "orig":
            continue
        stems_used = {s.get("stem") for s in b["shots"] if not s.get("mute") and not str(s.get("stem")).startswith("원음")}
        margins = [s.get("stem_margin", 9) for s in b["shots"] if not s.get("mute")]
        why = []
        raw = [sh for sh in b["shots"] if str(sh.get("stem")).startswith("원음")]
        if raw:  # 크기는 맞아도 배경음에 목소리가 묻혀 작게 들림
            why.append("원본 방송 소리 샷 " + ", ".join(f"{sh['start']:.1f}초" for sh in raw) + " — 목소리가 배경음에 묻힐 수 있음")
        if len(stems_used) > 1:
            why.append("분리본 섞임 " + "·".join(sorted(x for x in stems_used if x)))
        if margins and min(margins) < 0:
            why.append("분리 안 된 구간(소리 없음)")
        elif margins and min(margins) < 0.5:
            why.append(f"분리 가장자리 {min(margins):.2f}초(품질 위험)")
        a, z = b["shots"][0]["src"][0], b["shots"][-1]["src"][1]
        exp = " ".join(dict.fromkeys(o["en"] for o in plan["orig"]  # 조각마다 같은 영어가 반복되므로 한 번만
                                     if o["src"][0] < z and o["src"][1] > a))
        exp = re.sub(r"\([^)]*\)", "", exp).replace("/", " ")
        if mlx_whisper and exp.strip():
            wav = os.path.join(tmpd, f"b{i}.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{b['start']:.3f}", "-to", f"{b['end']:.3f}",
                            "-i", edit, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
            got = mlx_whisper.transcribe(wav, path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                         language="en", initial_prompt=exp)["text"]  # 짧은 조각 오인식 줄임
            sim = difflib.SequenceMatcher(None, words(exp), words(got)).ratio()
            if sim < 0.5:
                why.append(f"받아쓰기 불일치 {sim:.2f} — 들림 「{got.strip()[:60]}」")
        if why:
            audio_bad.append(f"  - {b['start']:.2f}~{b['end']:.2f} " + " / ".join(why))
    # 샷별 실제 크기: CapCut 볼륨 숫자가 같아도 원음 크기가 샷마다 다름 → 나레이션 크기 ±4 LU 밖이면 경고
    narr_i = None
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", os.path.join(ep, "narration.wav"),
                          "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    narr_i = float(m[-1]) if m else -18.5
    for b in tl["blocks"]:
        if b["kind"] != "orig":
            continue
        for sh in b["shots"]:
            if sh.get("mute") or sh["end"] - sh["start"] < 0.3:
                continue
            err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{sh['start']:.3f}", "-to",
                                  f"{sh['end']:.3f}", "-i", edit, "-vn", "-af", "ebur128", "-f", "null", "-"],
                                 capture_output=True, text=True).stderr
            m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
            if m and abs(float(m[-1]) - narr_i) > 4:
                audio_bad.append(f"  - {sh['start']:.2f}~{sh['end']:.2f} 크기 {float(m[-1]):.1f} LUFS — "
                                 f"나레이션({narr_i:.1f})과 {float(m[-1]) - narr_i:+.1f} LU 차이")
    lines.append(f"- 소리 검사(원본 대사 블록·샷 크기): 문제 {len(audio_bad)}개")
    lines += audio_bad
    issues += len(audio_bad)

    # 구조: 나레이션 비중 ≤ 50%(평균 40% 목표), 나레이션이 4컷 넘게 이어지면 지루함
    narr = sum(b["end"] - b["start"] for b in tl["blocks"] if b["kind"] == "narr")
    ratio = narr / tl["duration"]
    run, worst = 0, (0, 0.0)
    for b in tl["blocks"]:
        run = run + len(b["shots"]) if b["kind"] == "narr" else 0
        if run > worst[0]:
            worst = (run, b["start"])
    lines.append(f"- 나레이션 비중 {ratio * 100:.0f}% (목표 40%, 최대 50%) / 연속 나레이션 최대 {worst[0]}컷"
                 + (" ⚠" if ratio > 0.5 or worst[0] > 4 else ""))
    issues += (ratio > 0.5) + (worst[0] > 4)

    bad = []
    for e in subs:
        n = ko_len(e["ko"])
        d = e["end"] - e["start"]
        why = []
        if n > (15 if re.search(r"\d+(\.\d+)?(만|천)? ?(달러|원)", e["ko"]) else 12):
            why.append(f"{n:g}자")
        if d < 0.4:
            why.append(f"{d:.2f}초")
        if d > 0 and n / d > 12:
            why.append(f"초당 {n / d:.1f}자")
        if re.search(r"(?<!\d)[.,]|[.,](?!\d)", e["ko"]):
            why.append("온점·반점")
        if why:
            bad.append(f"  - {e['start']:.2f} 「{e['ko']}」 {' / '.join(why)}")
    lines.append(f"- 자막 {len(subs)}조각, 문제 {len(bad)}개")
    lines += bad
    issues += len(bad)
    nums = sorted({m for e in subs for m in re.findall(r"[\d.,%]+[^\s]*", e["ko"])})
    lines.append(f"- 🙋 숫자 확인: {' · '.join(nums)}")
    lines += ["", f"총 문제 {issues}개"]
    open(os.path.join(ep, "qa_report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
