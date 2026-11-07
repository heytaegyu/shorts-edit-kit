"""확정된 스토리보드 → card.json · cuts.json · subs_plan.json.

사용: python scripts/from_storyboard.py <회차>
입력: <회차>/work/storyboard/data.json (data.json, 없으면 rows/*.json)
      <회차>/work/storyboard/plan.json (제작 설정: 장면별 화면 shots·강조 kw·조각 시각 piece_src,
                                         join(앞 장면과 한 블록), react, flash_before, sfx)
스토리보드 표기: 문구 안 `/` = 다음 자막, 줄바꿈 = 같은 화면 아랫줄. 나레이션은 `/`·줄바꿈을 빼고 녹음.
반응컷(parent 있는 행)은 부모 장면 바로 뒤에 붙임.
"""
import json
import os
import sys


def load_rows(ep):
    data = os.path.join(ep, "work", "storyboard", "data.json")
    rows = []
    if os.path.exists(data):
        rows = json.load(open(data)).get("rows", [])
    else:
        d = os.path.join(ep, "work", "storyboard", "rows")
        for f in os.listdir(d):
            r = json.load(open(os.path.join(d, f)))
            r["id"] = f[:-5]
            rows.append(r)
    top = sorted((r for r in rows if not r.get("parent") and not r.get("removed")), key=lambda r: r["order"])
    out = []
    for r in top:
        out.append(r)
        out += sorted((k for k in rows if k.get("parent") == r["id"] and not k.get("removed")),
                      key=lambda k: k["order"])
    return out


def pieces_of(text):
    return [p.strip() for p in text.split("/") if p.strip()]


def main(ep):
    rows = load_rows(ep)
    plan = json.load(open(os.path.join(ep, "work", "storyboard", "plan.json")))
    P = plan["rows"]
    card_p, cuts_p, sp_p = (os.path.join(ep, f) for f in ("card.json", "cuts.json", "subs_plan.json"))
    card, cuts, sp = json.load(open(card_p)), json.load(open(cuts_p)), json.load(open(sp_p))

    blocks, script, narr_kw, narr_pieces, orig, row_block = [], [], {}, {}, [], {}
    line = 0
    for r in rows:
        cfg = P.get(r["id"], {})
        shots = cfg.get("shots") or ([{"src": r["src"], "cx": 0.5, "zoom": 1.0, "flip": False}] if r.get("src") else None)
        if not shots:
            sys.exit(f"{r['id']}: 화면(shots)이 없음 — plan.json에 넣을 것")
        if r["kind"] == "narr":
            voice = r["voice"]
            text = " ".join(voice.replace("/", " ").split())  # 녹음용: / · 줄바꿈 뺌
            blocks.append({"kind": "narr", "line": line, "row": r["id"], "shots": shots})
            script.append({"type": "narr", "text": text})
            if "/" in voice or "\n" in voice:  # 조각 안 줄바꿈 = 같은 화면 아랫줄(그대로 둠)
                narr_pieces[str(line)] = ["\n".join(" ".join(l.split()) for l in p.split("\n"))
                                          for p in pieces_of(voice)]
            narr_kw[str(line)] = cfg.get("kw", [])
            line += 1
        else:
            if cfg.get("join") and blocks and blocks[-1]["kind"] == "orig":
                blocks[-1]["shots"] += shots
                blocks[-1]["rows"].append(r["id"])
                blocks[-1]["keep_end"] = shots[-1]["src"][1]  # 말 없는 반응컷도 무음 정리로 잘리지 않게
            else:
                blocks.append({"kind": "orig", "rows": [r["id"]], "shots": shots, "note": r.get("stage", "")})
                script.append({"type": "orig", "note": r.get("sub", "")[:30]})
            if cfg.get("react"):
                blocks[-1]["react"] = True
            ps = pieces_of(r.get("sub") or r.get("caption", ""))  # 감탄 자막만 있는 반응컷
            srcs = cfg.get("piece_src")
            if not srcs:  # 장면 원본 구간을 글자 수 비율로 나눔
                a, b = r["src"]
                tot = sum(len(p.split("\n")[0]) for p in ps) or 1
                srcs, t = [], a
                for p in ps:
                    d = (b - a) * len(p.split("\n")[0]) / tot
                    srcs.append([round(t, 2), round(t + d, 2)]); t += d
            for p, s in zip(ps, srcs):
                orig.append({"src": s, "en": r.get("voice", ""), "ko": p, "kw": cfg.get("kw", []), "manual": True})
        row_block[r["id"]] = len(blocks) - 1

    cuts["blocks"] = blocks
    card["script"] = script
    card["title_screen"] = plan.get("title", card["title_screen"])
    card["flash_before_blocks"] = [row_block[x] for x in plan.get("flash_before", [])]
    card["sfx_cues"] = [[f"block:{row_block[a[4:]]}" if isinstance(a, str) and a.startswith("row:") else a, *rest]
                        for a, *rest in plan.get("sfx", [])]
    sp["orig"], sp["narr_keywords"], sp["narr_pieces"], sp["display"] = orig, narr_kw, narr_pieces, {}
    for p, o in ((card_p, card), (cuts_p, cuts), (sp_p, sp)):
        json.dump(o, open(p, "w"), ensure_ascii=False, indent=1)
    print(f"블록 {len(blocks)}개(나레이션 {line}줄), 원본 자막 {len(orig)}조각, "
          f"전환 블록 {card['flash_before_blocks']}")


if __name__ == "__main__":
    main(sys.argv[1])
