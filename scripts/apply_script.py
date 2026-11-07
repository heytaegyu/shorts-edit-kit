"""대본.txt ↔ card.json·subs_plan.json, 그리고 대본 반영 한 번에.

사용:
  python scripts/apply_script.py <회차> --export          # 지금 상태로 <회차>/대본.txt 만들기(덮어씀)
  python scripts/apply_script.py <회차> [--draft 이름]     # 대본.txt 반영: 바뀐 나레이션만 녹음 → 컷 길이
                                                         # → 자막 → 검수 → CapCut draft 수정(이름 주면)

대본.txt 문법
- 강조: {단어} 노랑 · {단어|green} · {단어|red} · {단어|green|16}(크기 16)
- 자막 조각 직접 끊기: " / " (없으면 자동)
- 음성과 다른 화면 자막(나레이션): 음성 문구 >> 화면 자막
- 대사: [16] 을 앞에 붙이면 그 대사 자막 크기 16
- 나레이션 줄 수·순서는 영상 블록과 짝 — 줄 추가·삭제·이동은 cuts.json도 함께 바꿔야 함
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
MARK = re.compile(r"\{([^{}|]+)(?:\|(\w+))?(?:\|(\d+))?\}")

HEADER = """# 대본 — 이 파일을 고치고 apply_script.py를 실행하면
#   바뀐 나레이션만 새로 녹음 → 컷 길이 다시 맞춤 → 자막 → CapCut 프로젝트까지 같이 바뀝니다.
#
# 쓰는 법
#  - 강조: {단어} 노랑 · {단어|green} 녹색 · {단어|red} 빨강 · {단어|green|16} 크기 16(포인트)
#  - 자막 조각을 직접 끊기: " / " (없으면 자동으로 9자 안팎)
#  - 음성과 다른 화면 자막: 음성 문구 >> 화면 자막   (예: 1만 4천 원에 … >> 1.4만 원에 …)
#  - 나레이션 금액은 원화로만. 대사(영어) 자막의 달러는 자동으로 아랫줄에 *원화*(녹색).
#  - 나레이션 줄 수·순서는 영상 컷과 짝이라 그대로 두세요. 줄 추가·삭제·이동은 cuts.json도 함께 고치세요.
#  - 대사 줄: 시각 | 영어 원문 | 한국어 자막   ([16] 을 앞에 붙이면 크기 16)
"""


def mark(text, kws):
    """키워드 목록을 {…} 표기로."""
    for k in sorted(kws, key=lambda k: -len(k[0] if isinstance(k, list) else k)):
        k = k if isinstance(k, list) else [k]
        word, color, size = k[0], (k[1] if len(k) > 1 else "yellow"), (k[2] if len(k) > 2 else None)
        tag = "{" + word + ("" if color == "yellow" and not size else f"|{color}") + \
            (f"|{size}" if size else "") + "}"
        if word in text and "{" + word not in text:
            text = text.replace(word, tag, 1)
    return text


def unmark(text):
    """{…} 표기 → (글, 키워드 목록)"""
    kws = []
    for m in MARK.finditer(text):
        k = [m.group(1)]
        if m.group(2) or m.group(3):
            k.append(m.group(2) or "yellow")
        if m.group(3):
            k.append(int(m.group(3)))
        kws.append(k if len(k) > 1 else k[0])
    return MARK.sub(lambda m: m.group(1), text), kws


def export(ep):
    card = json.load(open(os.path.join(ep, "card.json")))
    plan = json.load(open(os.path.join(ep, "subs_plan.json")))
    out = [HEADER, "[제목]", *card["title_screen"], "", "[나레이션]"]
    narr = [x for x in card["script"] if x["type"] == "narr"]
    disp = plan.get("display", {})
    for i, s in enumerate(narr):
        kws = plan["narr_keywords"].get(str(i), [])
        pieces = plan.get("narr_pieces", {}).get(str(i))
        if pieces and len(pieces) == 1 and pieces[0] in disp:
            line = f"{s['text']} >> {mark(disp[pieces[0]], kws)}"
        elif pieces:
            line = " / ".join(mark(p, kws) for p in pieces)
        else:
            line = mark(s["text"], kws)
        out.append(f"{i + 1:02d}. {line}")
    out += ["", "[대사]"]
    for o in plan["orig"]:
        ko = o["ko"]
        if o.get("fill"):
            ko = "{" + ko + f"|{o['fill']}" + (f"|{o['size']}" if o.get("size") else "") + "}"
        else:
            ko = mark(ko, o["kw"])
            if o.get("size") and not any(isinstance(k, list) and len(k) > 2 for k in o["kw"]):
                ko = f"[{o['size']}] {ko}"
        out.append(f"{o['src'][0]:.2f} | {o['en']} | {ko}")
    open(os.path.join(ep, "대본.txt"), "w").write("\n".join(out) + "\n")
    print("대본.txt 만듦")


def parse(ep):
    sec, title, narr, orig = None, [], [], []
    for raw in open(os.path.join(ep, "대본.txt")):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]") and "|" not in line:
            sec = line[1:-1]; continue
        if sec == "제목":
            title.append(line.strip())
        elif sec == "나레이션":
            narr.append(re.sub(r"^\d+\.\s*", "", line.strip()))
        elif sec == "대사":
            t, en, ko = [x.strip() for x in line.split("|", 2)]
            orig.append((float(t), en, ko))
    return title, narr, orig


def apply(ep):
    card_p, plan_p = os.path.join(ep, "card.json"), os.path.join(ep, "subs_plan.json")
    card, plan = json.load(open(card_p)), json.load(open(plan_p))
    title, narr, orig = parse(ep)
    slots = [x for x in card["script"] if x["type"] == "narr"]
    if len(narr) != len(slots):
        sys.exit(f"나레이션 줄 수가 {len(slots)} → {len(narr)}로 바뀜: 줄 추가·삭제는 "
                 "cuts.json도 함께 고치세요.")
    if len(title) == 2:
        card["title_screen"] = title
    kw_all, pieces_all, disp = {}, {}, {}
    for i, (slot, line) in enumerate(zip(slots, narr)):
        voice, _, shown = line.partition(">>")
        voice, kws = unmark(voice.strip())
        if shown:
            shown, kws2 = unmark(shown.strip())
            kws += kws2
            v = voice.replace(" / ", " ")
            pieces_all[str(i)] = [v]
            disp[v] = shown
            voice = v
        elif " / " in voice:
            pieces_all[str(i)] = [p.strip() for p in voice.split(" / ")]
            voice = " ".join(pieces_all[str(i)])
        slot["text"] = voice
        kw_all[str(i)] = kws
    plan["narr_keywords"], plan["narr_pieces"], plan["display"] = kw_all, pieces_all, disp
    by_t = {round(o["src"][0], 2): o for o in plan["orig"]}
    new_orig = []
    for t, en, ko in orig:
        o = by_t.get(round(t, 2))
        if o is None:
            print(f"⚠ {t:.2f} 대사는 원본 시각 정보가 없어 건너뜀(새 대사는 subs_plan.json에 직접 추가)"); continue
        size = None
        m = re.match(r"^\[(\d+)\]\s*", ko)
        if m:
            size, ko = int(m.group(1)), ko[m.end():]
        text, kws = unmark(ko)
        o.update(ko=text, kw=kws)
        o.pop("fill", None)
        o.pop("size", None)
        if size:
            o["size"] = size
        new_orig.append(o)
    plan["orig"] = new_orig
    json.dump(card, open(card_p, "w"), ensure_ascii=False, indent=1)
    json.dump(plan, open(plan_p, "w"), ensure_ascii=False, indent=1)
    print("card.json·subs_plan.json 갱신")


def run(ep, draft):
    steps = [["narrate.py", ep], ["render_edit.py", ep], ["make_subs.py", ep], ["qa.py", ep]]
    if draft:
        steps.append(["capcut_export.py", ep, draft, "--update",
                      "cuts,title,subs,video,narr,sfx,mix,order,media", "--relaunch"])
    for s in steps:
        print(f"── {s[0]}")
        subprocess.run([PY, os.path.join(HERE, s[0]), *s[1:]], check=True)


def main(ep, *opts):
    if "--export" in opts:
        return export(ep)
    apply(ep)
    run(ep, opts[opts.index("--draft") + 1] if "--draft" in opts else None)


if __name__ == "__main__":
    main(*sys.argv[1:])
