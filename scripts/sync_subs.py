"""싱크 맞춤 — 자막(원본 대사 조각)을 실제 말한 단어 시각에 맞춘다. make_subs.py 다음, qa.py 전에 돌린다.

사용: .venv/bin/python scripts/sync_subs.py <회차> [--check]
  --check : 고치지 않고 보고서만(work/sync_report.md)

하는 일 (원본 대사 자막만, 나레이션 자막은 ElevenLabs 정렬을 그대로 씀)
1. src_words.json 단어를 timeline.json으로 편집본 시각에 옮긴다.
2. 같은 대사(en)에서 나온 조각 묶음마다
   - 묶음 시작 = 첫 단어 시작, 묶음 끝 = 마지막 단어 끝 + 0.5초(다음 자막 전까지)
   - 조각 경계 = 단어 사이 틈으로 옮김. 조각 속 숫자·영문(25%, $35,000, CEO)이 실제로 그 말을
     할 때 보이도록 경계를 정하고, 그럴 수 없으면(한국어 어순이 반대) 보고서에 「어순」으로 남긴다.
3. 다음 조각과 0.3초 안이면 붙이고, 최소 노출(MIN_DUR)을 지킨다.
4. subs.json, edit.ko.srt, edit.en.srt를 다시 쓰고 work/sync_report.md에 전후 차이를 남긴다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from make_subs import MIN_DUR, src_to_edit, write_srt  # noqa: E402

FPS = 30
TAIL = 2 / FPS          # 조각 사이: 앞 단어 끝 + 2프레임
END_HOLD = 0.5          # 묶음 끝: 마지막 단어 끝 + 0.5초(다음 자막이 오면 거기까지, rules/subtitles.md 타이밍)
MOVE_MIN = 0.06         # 이보다 작은 차이는 그대로
NEAR = 0.6              # 경계 후보를 찾는 범위(초)


# ── 숫자·영문 기준점 ─────────────────────────────────────────
def ko_numbers(text):
    """한국어 조각의 숫자(*약 …원* 줄 제외). 「3만 5천」→35000, 「25%」→25, 「35000」→35000."""
    text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("*"))
    text = text.replace(",", "")
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*만(?:\s*(\d+)\s*천)?|(\d+(?:\.\d+)?)\s*천|(\d+(?:\.\d+)?)", text):
        if m.group(1):
            out.append(float(m.group(1)) * 10000 + (float(m.group(2)) * 1000 if m.group(2) else 0))
        elif m.group(3):
            out.append(float(m.group(3)) * 1000)
        else:
            out.append(float(m.group(4)))
    return out


def ko_latin(text):
    return [w.lower() for w in re.findall(r"[A-Za-z]{2,}", text)]


def en_tokens(words):
    """단어 목록 → [(숫자 또는 None, 영문 소문자, 단어 인덱스들)]. Whisper가 「$35」「,000」으로 쪼갠 숫자를 합친다."""
    toks, i = [], 0
    while i < len(words):
        w = words[i]["w"]
        if re.match(r"^[\$]?\d", w) or (re.match(r"^[,.]\d", w) and toks and toks[-1][0] is not None):
            j, s = i, ""
            while j < len(words) and (j == i or re.match(r"^[,.]?\d|^%|^-\d|^K\b", words[j]["w"])):
                s += words[j]["w"]; j += 1
            num = re.sub(r"[^\d.Kk-]", "", s.split("-")[0])
            mult = 1000 if num.lower().endswith("k") else 1
            try:
                val = float(num.rstrip("Kk").strip(".")) * mult
            except ValueError:
                val = None
            toks.append((val, None, list(range(i, j)))); i = j
        else:
            toks.append((None, re.sub(r"[^a-z]", "", w.lower()), [i])); i += 1
    return toks


def anchors(piece, words):
    """조각 속 숫자·영문이 묶음 단어 중 몇 번째에서 말해지는지(단어 인덱스 목록)."""
    toks = en_tokens(words)
    hit = []
    for n in ko_numbers(piece):
        for val, _, idx in toks:
            if val is not None and abs(val - n) < 1e-6:
                hit.append(idx[0]); break
    for lw in ko_latin(piece):
        for val, w, idx in toks:
            if w and w == lw:
                hit.append(idx[0]); break
    return hit


# ── 본체 ────────────────────────────────────────────────────
def edit_words(ep, tl):
    src = json.load(open(os.path.join(ep, "work", "src_words.json")))
    out = []
    for seg in src["segments"]:
        for w in seg["words"]:
            a, b = src_to_edit(tl["blocks"], w["s"], tol=0), src_to_edit(tl["blocks"], w["e"], tol=0)
            if a is not None and b is not None and b > a + 0.02:
                out.append({"w": w["w"].strip(), "s": a, "e": b})
    out.sort(key=lambda x: x["s"])
    return out


def groups_of(events):
    """연속한 원본 조각 중 같은 en 대사끼리 묶기."""
    gs, cur = [], None
    for i, e in enumerate(events):
        if e["kind"] != "orig":
            cur = None; continue
        if cur and events[cur[-1]]["en"] == e["en"] and e["start"] - events[cur[-1]]["end"] < 0.5:
            cur.append(i)
        else:
            cur = [i]; gs.append(cur)
    return gs


def sync(ep, check=False):
    tl = json.load(open(os.path.join(ep, "timeline.json")))
    subs_p = os.path.join(ep, "subs.json")
    events = json.load(open(subs_p))["events"]
    W = edit_words(ep, tl)
    before = [(e["start"], e["end"]) for e in events]
    notes = []

    for g in groups_of(events):
        es = [events[i] for i in g]
        lo, hi = es[0]["start"] - 0.35, es[-1]["end"] + 0.15
        # 같은 원본 블록 안 단어만(옆 블록 대사가 섞이지 않게)
        blk = next((b for b in tl["blocks"] if b["kind"] == "orig" and b["start"] - 0.05 <= es[0]["start"] < b["end"]), None)
        if blk:
            lo, hi = max(lo, blk["start"] - 0.02), min(hi, blk["end"] + 0.02)
        ws = [w for w in W if lo <= (w["s"] + w["e"]) / 2 <= hi]
        if not ws:
            notes.append(f"| {es[0]['start']:.2f} | {es[0]['ko'].splitlines()[0]} | 말소리 못 찾음 — 그대로 |")
            continue
        start, end = ws[0]["s"], ws[-1]["e"] + END_HOLD
        # 조각 경계: 단어 사이 틈 후보
        gaps = [((ws[k]["e"] + ws[k + 1]["s"]) / 2, ws[k + 1]["s"] - ws[k]["e"], k + 1) for k in range(len(ws) - 1)]
        bounds = []
        prev_k = 0
        for j in range(1, len(es)):
            target = es[j]["start"]
            need_after = max(anchors(es[j - 1]["ko"], ws) or [-1])      # 앞 조각 기준 단어는 경계 앞
            need_before = min(anchors(es[j]["ko"], ws) or [len(ws)])    # 뒤 조각 기준 단어는 경계 뒤
            ok = [c for c in gaps if c[2] > prev_k and c[2] > need_after and c[2] <= need_before]
            if not ok:
                ok = [c for c in gaps if c[2] > prev_k]
                if need_after >= need_before:
                    notes.append(f"| {es[j - 1]['start']:.2f} | {es[j - 1]['ko'].splitlines()[0]} / {es[j]['ko'].splitlines()[0]}"
                                 f" | 어순 — 영어는 「{ws[need_before]['w']}」가 「{ws[need_after]['w']}」보다 먼저. 조각 순서·문구를 영어 순서로 |")
            if not ok:
                bounds.append((target, target)); continue
            near = [c for c in ok if abs(c[0] - target) <= NEAR] or ok
            best = min(near, key=lambda c: abs(c[0] - target) - min(c[1], 0.3))  # 가깝고 틈이 클수록
            k = best[2]  # 앞 조각은 앞 단어 끝+꼬리까지, 뒤 조각은 다음 단어 시작부터
            bounds.append((min(ws[k - 1]["e"] + TAIL, ws[k]["s"]), ws[k]["s"])); prev_k = k
        bounds = [(None, start)] + bounds + [(end, None)]
        for j, e in enumerate(es):
            s, t = bounds[j][1], bounds[j + 1][0]
            if t - s < 0.25:  # 너무 짧아지면 원래 길이 비율로 되돌림
                continue
            if abs(s - e["start"]) >= MOVE_MIN:
                e["start"] = s
            if abs(t - e["end"]) >= MOVE_MIN:
                e["end"] = t

    # 겹침·틈 정리(make_subs와 같은 규칙)
    events.sort(key=lambda e: e["start"])
    for i, e in enumerate(events):
        nxt = events[i + 1]["start"] if i + 1 < len(events) else tl["duration"]
        if e["end"] > nxt or nxt - e["end"] < 0.3:
            e["end"] = nxt
        if e["end"] - e["start"] < MIN_DUR:
            e["end"] = min(e["start"] + MIN_DUR, nxt)
        e["start"], e["end"] = round(max(e["start"], 0.0), 3), round(e["end"], 3)

    rows = []
    for (a0, b0), e in zip(before, events):
        da, db = e["start"] - a0, e["end"] - b0
        if e["kind"] == "orig" and (abs(da) >= MOVE_MIN or abs(db) >= MOVE_MIN):
            rows.append(f"| {a0:.2f}–{b0:.2f} → {e['start']:.2f}–{e['end']:.2f} | {da:+.2f} / {db:+.2f} | "
                        f"{e['ko'].splitlines()[0]} |")
    rep = ["# 싱크 맞춤 보고서", "", f"- 원본 대사 조각 {sum(e['kind'] == 'orig' for e in events)}개 중 {len(rows)}개 이동"
           + (" (--check: 파일은 그대로)" if check else ""), "",
           "## 이동", "", "| 전 → 후 | 시작/끝 차이 | 자막 |", "|---|---|---|", *rows, "",
           "## 사람이 볼 것", "", "| 시각 | 자막 | 내용 |", "|---|---|---|", *notes]
    os.makedirs(os.path.join(ep, "work"), exist_ok=True)
    open(os.path.join(ep, "work", "sync_report.md"), "w").write("\n".join(rep) + "\n")
    print("\n".join(rep))
    if check:
        return
    json.dump({"events": events}, open(subs_p, "w"), ensure_ascii=False, indent=1)
    write_srt(os.path.join(ep, "edit.ko.srt"), events, "ko")
    en, last = [], None
    for e in events:
        if not e["en"]:
            continue
        if last and last["en"] == e["en"]:
            last["end"] = e["end"]
        else:
            last = dict(e); en.append(last)
    write_srt(os.path.join(ep, "edit.en.srt"), en, "en")


if __name__ == "__main__":
    sync(sys.argv[1].rstrip("/"), check="--check" in sys.argv)
