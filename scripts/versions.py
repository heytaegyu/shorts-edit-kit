"""회차 버전: 회차 파일 + CapCut draft를 한 묶음으로 저장·되돌리기.

사용:
  python scripts/versions.py <회차> save <draft> "메모" [NN]  # 지금 상태를 vNN으로 저장(번호 생략 = 다음)
  python scripts/versions.py <회차> list
  python scripts/versions.py <회차> restore <draft> NN       # vNN으로 되돌림(되돌리기 전 상태도 먼저 저장)

저장 위치: <회차>/work/versions/vNN_<MMDD_HHMM>/
- 회차 파일: card·cuts·subs_plan·subs·timeline·narration.json, 대본.txt, qa_report.md, edit.mp4,
  narration.wav, preview.mp4, narration/*.mp3 (mp3는 줄이 바뀌면 덮어써지므로 같이 보관)
- CapCut: draft_info.json, draft_meta_info.json
되돌릴 때 CapCut draft의 타임라인 id는 지금 것을 유지(폴더 구조와 맞춤). CapCut이 켜져 있으면 종료 → 쓰기 → 다시 열기.
"""
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capcut_export as cc  # noqa: E402

FILES = ["card.json", "cuts.json", "subs_plan.json", "subs.json", "timeline.json", "narration.json",
         "대본.txt", "qa_report.md", "edit.mp4", "narration.wav", "preview.mp4"]
DRAFT_FILES = ["draft_info.json", "draft_meta_info.json"]


def vroot(ep):
    return os.path.join(ep, "work", "versions")


def listing(ep):
    root = vroot(ep)
    if not os.path.isdir(root):
        return []
    return sorted(x for x in os.listdir(root) if re.match(r"v\d\d_", x))


def save(ep, draft, note="", n=None):
    os.makedirs(vroot(ep), exist_ok=True)
    n = n or 1 + max([int(x[1:3]) for x in listing(ep)], default=0)
    vd = os.path.join(vroot(ep), f"v{n:02d}_{time.strftime('%m%d_%H%M')}")
    os.makedirs(os.path.join(vd, "capcut"))
    for f in FILES:
        if os.path.exists(os.path.join(ep, f)):
            shutil.copy2(os.path.join(ep, f), vd)
    if os.path.isdir(os.path.join(ep, "narration")):
        shutil.copytree(os.path.join(ep, "narration"), os.path.join(vd, "narration"))
    dst = os.path.join(cc.ROOT, cc.resolve_draft(draft))
    for f in DRAFT_FILES:
        if os.path.exists(os.path.join(dst, f)):
            shutil.copy2(os.path.join(dst, f), os.path.join(vd, "capcut"))
    json.dump({"version": n, "time": time.strftime("%Y-%m-%d %H:%M"), "draft": draft, "note": note},
              open(os.path.join(vd, "version.json"), "w"), ensure_ascii=False, indent=1)
    print(f"저장: v{n:02d} — {note}")
    return n


def restore(ep, draft, n):
    vd = os.path.join(vroot(ep), next(x for x in listing(ep) if x.startswith(f"v{int(n):02d}_")))
    relaunch = cc.capcut_running()
    if relaunch and not cc.quit_capcut():
        sys.exit("CapCut이 종료되지 않았습니다.")
    save(ep, cc.resolve_draft(draft), f"v{int(n):02d}로 되돌리기 직전 상태")
    for f in FILES:
        if os.path.exists(os.path.join(vd, f)):
            shutil.copy2(os.path.join(vd, f), ep)
    if os.path.isdir(os.path.join(vd, "narration")):
        # 버전의 mp3로 맞춤(지금 폴더에만 있는 mp3는 그대로 둠 — narration.json이 가리키지 않음)
        for f in os.listdir(os.path.join(vd, "narration")):
            shutil.copy2(os.path.join(vd, "narration", f), os.path.join(ep, "narration", f))
    draft = cc.resolve_draft(draft)
    dst = os.path.join(cc.ROOT, draft)
    cur = json.load(open(os.path.join(dst, "draft_info.json")))
    d = json.load(open(os.path.join(vd, "capcut", "draft_info.json")))
    d["id"] = cur["id"]
    cc.save(dst, d, draft, False)
    draft = cc.rename_draft(draft, f"{cc.base_name(draft)}_v{int(n):02d}")  # 이름도 그 버전으로
    if relaunch:
        cc.open_capcut()
    print(f"되돌림: {os.path.basename(vd)}")


def main(ep, cmd, *args):
    if cmd == "save":
        save(ep, args[0], args[1] if len(args) > 1 else "", int(args[2]) if len(args) > 2 else None)
    elif cmd == "list":
        for x in listing(ep):
            v = json.load(open(os.path.join(vroot(ep), x, "version.json")))
            print(f"{x}  {v['note']}")
    elif cmd == "restore":
        restore(ep, args[0], args[1])


if __name__ == "__main__":
    main(*sys.argv[1:])
