"""자막: 나레이션(TTS 글자 타이밍) + 원본 대사(subs_plan) → subs.json, edit.ko.srt, edit.en.srt

사용: python scripts/make_subs.py <회차 폴더>
- 조각 길이: 공백 제외 목표 9자, 최대 12자(레퍼런스 실측 중앙값 9자, 5~12자)
- 색 강조 단어(kw)는 조각 안에서 끊지 않음
- 한국어: 온점·반점 제거, 물음표·느낌표 유지
- 나레이션은 원화로만(대본에서 원화로 씀). 원본 대사(영어)의 달러만: 「5만 달러에 사려다」 한 조각 +
  아랫줄 「*7천만 원*」(녹색, card.json krw_rate). 달러는 소수점 1자리.
  원화는 간결하게: 1,400원 → 「1400원」, 1.4만 원, 14만 원, 140만 원, 7천만 원. 돈이 든 조각은 돈 바로 뒤에서 끊지 않음.
- 조각 직접 지정: subs_plan narr_pieces {"줄 번호": ["조각", ...]} (자동 분할 대신)
- 표시 문구 바꾸기: subs_plan display {"나레이션 조각": "화면에 쓸 짧은 문구"} (음성은 그대로, 폭 줄이기용)
- 폭: 한 줄이 「가격 20%에 5만 원으로 사려다」(글자 크기 14) 폭을 넘으면 크기를 줄이고 경고 — 문구를 먼저 줄일 것
- 애니메이션(anim): 빨강 강조 = Zoom In 0.8초 / 크기 16 이상 강조 = Zoom In 0.5초 이상(조각이 길면 길게, 최대 1초)
  / 0.8초 미만으로 빨리 지나가는 조각 = 없음 / 나머지 = 프리셋 페이드
- 키워드: "단어" (노랑) | ["단어", "green|red|yellow"] | ["단어", 색, 글자 크기]
  orig 항목은 "fill"(전체 색), "size"(리액션·포인트 16, 비명 20)도 받음. 기본 크기 14.
"""
import json
import os
import re
import sys

import kit_config

TARGET, MAX = 9, 12
MIN_DUR = 0.4
BASE_SIZE = 14
JOIN = "\u0001"  # 여러 단어 키워드를 한 토큰으로 묶을 때만 쓰는 임시 문자
WIDTH_REF = ("가격 20%에 5만 원으로 사려다", 14)  # 사용자 실측: 좌우 여백을 남기는 최대 폭
MONEY = re.compile(r"(\d+(?:\.\d+)?)\s?(만|천)?\s?달러")


def nchar(s):
    return len(s.replace(" ", ""))


def won(x):
    """원화를 간결하게(유효숫자 2자리): 1400원, 1.4만 원, 14만 원, 140만 원, 7천만 원, 1.4억 원."""
    x = float(f"{x:.2g}")
    if x >= 1e8:
        return f"{x / 1e8:g}억 원"
    if x >= 1e7 and x % 1e7 == 0:
        return f"{int(x // 1e7)}천만 원"
    if x >= 1e4:
        return f"{x / 1e4:g}만 원"
    return f"{int(x)}원"


def money(text, rate):
    """「N달러」를 소수점 1자리로 고치고 원화 목록. → (새 글, [(달러 표기, 원화)])"""
    found = []

    def rep(m):
        num = float(m.group(1)) * {"만": 1e4, "천": 1e3, None: 1}[m.group(2)]
        usd = f"{round(float(m.group(1)), 1):g}{m.group(2) or ''}{' ' if m.group(2) else ''}달러"
        found.append((usd, won(num * rate)))
        return usd
    return MONEY.sub(rep, text), found


def with_won(piece, found):
    """돈이 든 조각 아랫줄에 *원화*."""
    wons = [w for usd, w in found if usd in piece]
    return piece + ("\n" + " ".join(f"*{w}*" for w in wons) if wons else "")


def norm_kws(kws):
    """키워드 표기를 (단어, 색, 크기)로."""
    out = []
    for k in kws:
        k = [k] if isinstance(k, str) else list(k)
        out.append((k[0], k[1] if len(k) > 1 else "yellow", k[2] if len(k) > 2 else None))
    return out


def tokens(text, kws):
    """공백 단위 토큰. 여러 단어로 된 키워드는 한 토큰으로 묶음."""
    for k in sorted(kws, key=len, reverse=True):
        if " " in k:
            text = text.replace(k, k.replace(" ", JOIN))
    return [t.replace(JOIN, " ") for t in text.split(" ")]


# 조각 끝에 오면 안 되는 꾸밈말(뒤 명사와 붙어야 함), 조각 첫머리에 오면 안 되는 의존명사
NO_END = {"이", "그", "저", "같은", "다른", "만든", "내민", "다치는", "새", "그냥"}
NO_START = {"게", "거", "것", "수", "줄", "데", "때"}


def chunk(text, kws, money_words=()):
    """목표 글자 수에 가깝게, 최대 글자 수 안에서, 꾸밈말–명사를 끊지 않는 분할(동적 계획).
    돈(money_words)이 든 조각은 최대 +3자, 돈 바로 뒤에서 끊으면 벌점(돈과 뒷말을 한 번에 보이게)."""
    toks = tokens(text, kws)
    n = len(toks)
    best = [0.0] + [float("inf")] * n
    prev = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            piece = " ".join(toks[i:j])
            has_money = any(m in piece for m in money_words)
            c = nchar(piece)
            if c > MAX + (3 if has_money else 0) and j - i > 1:
                break
            cost = (c - TARGET) ** 2
            if j < n and toks[j - 1] in NO_END:
                cost += 100
            if toks[i] in NO_START:
                cost += 100
            if j < n and any(m.split(" ")[-1] in toks[j - 1] for m in money_words):
                cost += 60
            if best[i] + cost < best[j]:
                best[j], prev[j] = best[i] + cost, i
    out, j = [], n
    while j > 0:
        out.append(" ".join(toks[prev[j]:j])); j = prev[j]
    return out[::-1]


def word_spans(line):
    """정렬 글자열(원문 또는 로마자 정규화)을 공백으로 나눠 원문 단어별 (시작, 끝) 시간."""
    spans, cur = [], None
    for ch, a, z in zip(line["chars"], line["starts"], line["ends"]):
        if ch.isspace():
            if cur:
                spans.append(cur); cur = None
        else:
            cur = [cur[0], z] if cur else [a, z]
    if cur:
        spans.append(cur)
    words = line["text"].split(" ")
    if len(spans) != len(words):  # 단어 수가 다르면 글자 수 비례로 근사
        total, t, spans = sum(len(w) for w in words), 0.0, []
        for w in words:
            d = line["dur"] * len(w) / total
            spans.append([t, t + d]); t += d
    return spans


def clean_ko(s):
    """온점·반점 제거. 단 숫자 사이(1.69, 1,000)는 유지."""
    return re.sub(r"(?<!\d)[.,]|[.,](?!\d)", "", s).strip()


def kw_in(piece, kws):
    return [[k, c] for k, c, _ in kws if k in piece]


def size_of(piece, kws, base=BASE_SIZE):
    return max([base] + [z for k, _, z in kws if z and k in piece])


def fit_width(events):
    """줄 폭이 최대 폭을 넘으면 글자 크기를 줄임(정수 내림)."""
    from PIL import ImageFont
    f = ImageFont.truetype(kit_config.font(), 100)
    limit = f.getlength(WIDTH_REF[0]) * WIDTH_REF[1]
    for e in events:
        r = max(f.getlength(line) for line in e["ko"].split("\n")) * e["size"] / limit
        if r > 1.0:
            new = int(e["size"] / r)
            print(f"⚠ 폭 {r:.2f}배 — 크기 {e['size']}→{new}: {e['ko']!r} (문구를 줄이는 게 우선)")
            e["size0"], e["size"] = e["size"], new  # 강조 여부(애니메이션)는 줄이기 전 크기로 판단


def anim_of(e):
    """자막 등장 애니메이션: {"in": "zoom"|"fade"|None, "dur": 초}"""
    d = e["end"] - e["start"]
    red = any(c == "red" for _, c in e["kw"])
    if red:
        return {"in": "zoom", "dur": round(min(0.8, max(0.5, d - 0.15)), 2)}
    if e.get("size0", e["size"]) >= 16:
        return {"in": "zoom", "dur": round(min(1.0, max(0.5, d * 0.5)), 2)}
    if d < 0.8:
        return {"in": None, "dur": 0}
    return {"in": "fade", "dur": 0.23}


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(path, events, key):
    with open(path, "w") as f:
        for i, e in enumerate(events, 1):
            f.write(f"{i}\n{srt_time(e['start'])} --> {srt_time(e['end'])}\n{e[key]}\n\n")


def src_to_edit(blocks, t, tol=0.6):
    """원본 시각 → 편집본 시각. 1) 샷 안에 있으면 그대로 2) 블록 앞뒤가 무음 정리로 잘렸으면
    tol 안에서 가장 가까운 블록 끝으로 붙임."""
    orig = [b for b in blocks if b["kind"] == "orig"]
    for b in orig:
        for s in b["shots"]:
            if s["src"][0] - 0.05 <= t <= s["src"][1] + 0.05:
                return min(max(s["start"] + (t - s["src"][0]), b["start"]), b["end"])
    # 블록 안 샷 사이(무음 정리로 잘린 틈)에 떨어지면 다음 샷 시작으로 — 안 그러면 그 조각 자막이 통째로 빠짐
    for b in orig:
        sh = b["shots"]
        for s, n in zip(sh, sh[1:]):
            if s["src"][1] < t < n["src"][0]:
                return n["start"]
    best = None
    for b in orig:
        first, last = b["shots"][0]["src"][0], b["shots"][-1]["src"][1]
        for gap, at in ((first - t, b["start"]), (t - last, b["end"])):
            if 0 < gap <= tol and (best is None or gap < best[0]):
                best = (gap, at)
    return best[1] if best else None


def main(ep):
    tl = json.load(open(os.path.join(ep, "timeline.json")))
    narr = {l["i"]: l for l in json.load(open(os.path.join(ep, "narration.json")))["lines"]}
    plan = json.load(open(os.path.join(ep, "subs_plan.json")))
    rate = json.load(open(os.path.join(ep, "card.json"))).get("krw_rate", 1400)
    events = []

    for b in tl["blocks"]:
        if b["kind"] != "narr":
            continue
        line = narr[b["line"]]
        kws = norm_kws(plan["narr_keywords"].get(str(b["line"]), []))
        spans = word_spans(line)
        text = line["text"]  # 나레이션은 원화로만 말하고 원화로만 씀(달러 동시 표기 X)
        pieces = plan.get("narr_pieces", {}).get(str(b["line"])) or \
            chunk(text, [k for k, _, _ in kws])
        wi = 0
        for piece in pieces:
            n = len(piece.split())  # 조각 안 줄바꿈도 단어 경계
            s0, s1 = spans[wi][0], spans[min(wi + n, len(spans)) - 1][1]
            wi += n
            shown = plan.get("display", {}).get(piece, piece)
            ko = "\n".join(clean_ko(x) for x in shown.split("\n"))
            events.append({"kind": "narr", "start": b["narr_start"] + s0,
                           "end": b["narr_start"] + s1, "ko": ko,
                           "en": "", "kw": kw_in(ko, kws), "size": size_of(shown, kws),
                           "block_end": b["end"]})

    for o in plan["orig"]:
        a, z = src_to_edit(tl["blocks"], o["src"][0]), src_to_edit(tl["blocks"], o["src"][1])
        if a is None or z is None or z - a < 0.2:
            print("편집본에 없는 원본 대사(건너뜀):", o["en"])
            continue
        if o.get("manual"):  # 스토리보드에서 사람이 쓴 조각 그대로(아랫줄 *원화*까지 직접 씀)
            ko = "\n".join(clean_ko(x) for x in o["ko"].split("\n"))
            found = []
            kws = norm_kws(o["kw"]) + [(m, "green", None) for m in re.findall(r"\*[^*]+\*", ko)] + \
                [(m.group(0), "yellow", None) for m in MONEY.finditer(ko)]
            pieces = [ko]
        else:
            ko, found = money(clean_ko(o["ko"]), rate)
            kws = norm_kws(o["kw"]) + [(usd, "yellow", None) for usd, _ in found] + \
                [(f"*{w}*", "green", None) for _, w in found]
            pieces = chunk(ko, [k for k, _, _ in kws], [u for u, _ in found])
        total = sum(nchar(p) for p in pieces)
        t = a
        for p in pieces:
            d = (z - a) * nchar(p) / total
            text = with_won(p, found)
            kw = [[p, o["fill"]]] + kw_in(text, kws)[len(kw_in(p, kws)):] if o.get("fill") \
                else kw_in(text, kws)
            events.append({"kind": "orig", "start": t, "end": t + d, "ko": text, "en": o["en"],
                           "kw": kw, "size": size_of(p, kws, o.get("size", BASE_SIZE)),
                           "block_end": z + 0.3})
            t += d

    # 타이밍 정리: 다음 조각까지 붙이되(0.3초 이내 간격) 블록을 넘지 않게, 최소 노출 보장
    events.sort(key=lambda e: e["start"])
    for i, e in enumerate(events):
        nxt = events[i + 1]["start"] if i + 1 < len(events) else tl["duration"]
        if nxt - e["end"] < 0.3:
            e["end"] = nxt
        else:
            e["end"] = min(e["end"] + 0.15, nxt, e["block_end"])
        if e["end"] - e["start"] < MIN_DUR:
            e["end"] = min(e["start"] + MIN_DUR, nxt)
        e["start"], e["end"] = round(max(e["start"], 0.0), 3), round(e["end"], 3)
        e.pop("block_end")
    fit_width(events)
    for e in events:
        e["anim"] = anim_of(e)

    json.dump({"events": events}, open(os.path.join(ep, "subs.json"), "w"),
              ensure_ascii=False, indent=1)
    write_srt(os.path.join(ep, "edit.ko.srt"), events, "ko")
    write_srt(os.path.join(ep, "edit.en.srt"), [e for e in events if e["en"]], "en")
    # en은 같은 대사가 여러 조각으로 나뉘면 중복 → 합치기
    en, last = [], None
    for e in events:
        if not e["en"]:
            continue
        if last and last["en"] == e["en"]:
            last["end"] = e["end"]
        else:
            last = dict(e); en.append(last)
    write_srt(os.path.join(ep, "edit.en.srt"), en, "en")
    for e in events:
        print(f"{e['start']:6.2f}-{e['end']:6.2f} {e['kind']:4} {nchar(e['ko']):2}자 {e['size']} "
              f"{e['anim']['in'] or '-':4} {e['ko']!r}  {e['kw']}")


if __name__ == "__main__":
    main(sys.argv[1])
