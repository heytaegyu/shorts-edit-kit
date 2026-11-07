"""가상 예시로 파이프라인을 끝까지 돌려 보는 점검. 실제 영상·음성·대본·유료 API를 쓰지 않는다.

사용: python tests/run_demo.py --font <한글 글꼴 파일> [--keep]
- ffmpeg로 색 화면·사인파 소리를 만들어 임시 폴더에서만 작업한다. 실제 CapCut 프로젝트 폴더는 건드리지 않는다.
- voice_id와 키를 비워 둬서 나레이션을 새로 녹음하려 하면 API 호출 전에 멈춘다.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
NARR = ["가상의 컵 하나가 혼자 움직인다는데", "정말 그런지 끝까지 보여준다는데"]
LINES = [("This is a fake line about 25 percent.", "이건 가짜 대사예요 25%"),
         ("Nobody ever said this sentence.", "아무도 한 적 없는 말이에요")]


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def tone(path, dur, freq=220):
    ff("-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", "48000",
       "-af", "volume=0.3", path)


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def source(ep):
    os.makedirs(os.path.join(ep, "source"))
    os.makedirs(os.path.join(ep, "audio"))
    colors = ["red", "green", "blue", "yellow", "purple", "orange"]
    inputs = []
    for c in colors:
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=1920x1080:r=30:d=5"]
    chain = "".join(f"[{i}:v]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1:a=0[v]"
    gate = "volume='if(lt(mod(t,3),2.6),0.3,0)':eval=frame"
    ff(*inputs, "-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-filter_complex",
       f"{chain};[{len(colors)}:a]{gate},aformat=channel_layouts=stereo:sample_rates=48000[a]",
       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
       os.path.join(ep, "source", "demo.mp4"))
    ff("-i", os.path.join(ep, "source", "demo.mp4"), "-vn", "-c:a", "pcm_s24le",
       os.path.join(ep, "audio", "dx.wav"))


def narration(ep):
    d = os.path.join(ep, "narration")
    os.makedirs(d)
    lines = []
    for i, text in enumerate(NARR):
        p = os.path.join(d, f"{i:02d}.mp3")
        tone(p, 2.5, 330)
        n = len(text)
        lines.append({"i": i, "text": text, "file": p, "dur": 2.5, "chars": list(text),
                      "starts": [round(2.5 * k / n, 3) for k in range(n)],
                      "ends": [round(2.5 * (k + 1) / n, 3) for k in range(n)]})
    dump(os.path.join(ep, "narration.json"), {"voice_id": "", "settings": {}, "lines": lines})


def storyboard(ep):
    rows = [{"id": "n1", "order": 1, "scene": 1, "kind": "narr", "voice": "가상의 컵 하나가 / 혼자 움직인다는데"},
            {"id": "o1", "order": 2, "scene": 1, "kind": "orig", "sub": LINES[0][1], "voice": LINES[0][0],
             "src": [6.0, 8.5], "stage": "가상 장면"},
            {"id": "n2", "order": 3, "scene": 2, "kind": "narr", "voice": NARR[1]},
            {"id": "o2", "order": 4, "scene": 2, "kind": "orig", "sub": LINES[1][1], "voice": LINES[1][0],
             "src": [16.0, 18.5], "stage": "가상 장면"}]
    shot = lambda a, b, flip=False: {"src": [a, b], "cx": 0.5, "zoom": 1.0, "flip": flip}
    plan = {"title": ["가상의 컵이 움직인다고?!", "끝까지 봐야 아는 이유"],
            "flash_before": ["o1"], "sfx": [["row:o2", "file_sfx"]],
            "rows": {"n1": {"shots": [shot(1.0, 4.0)], "kw": ["컵"]},
                     "o1": {"shots": [shot(6.0, 8.5, True)], "kw": [["25%", "red"]]},
                     "n2": {"shots": [shot(11.0, 14.0)], "kw": []},
                     "o2": {"shots": [shot(16.0, 18.5)], "kw": []}}}
    sb = os.path.join(ep, "work", "storyboard")
    dump(os.path.join(sb, "data.json"), {"rows": rows})
    dump(os.path.join(sb, "plan.json"), plan)
    dump(os.path.join(ep, "card.json"), {"title_screen": plan["title"], "krw_rate": 1400, "script": []})
    dump(os.path.join(ep, "cuts.json"), {"source": "source/demo.mp4", "content_rect": [0, 0, 1920, 1080], "blocks": []})
    dump(os.path.join(ep, "subs_plan.json"), {"narr_keywords": {}, "orig": []})
    words = []
    for (text, _), start in zip(LINES, (6.1, 16.1)):
        toks = text.split()
        step = 2.0 / len(toks)
        words.append({"start": start, "end": start + 2.0, "text": text,
                      "words": [{"w": w, "s": round(start + k * step, 3), "e": round(start + (k + 1) * step, 3), "p": 1}
                                for k, w in enumerate(toks)]})
    dump(os.path.join(ep, "work", "src_words.json"), {"src": "source/demo.mp4", "segments": words})


def template(root, sfx):
    """CapCut 없이 쓰기 코드를 돌려 보려고 만든 최소 가상 템플릿. 실제 CapCut 호환성을 보장하지 않는다."""
    d = os.path.join(root, "template")
    os.makedirs(d)
    mats = {"videos": [], "texts": [], "audios": [], "stickers": [], "material_animations": [], "audio_fades": []}
    n = [0]

    def mat(kind, **kw):
        n[0] += 1
        o = {"id": f"M{n[0]}", **kw}
        mats[kind].append(o)
        return o["id"]

    def seg(mid, refs, dur=1_000_000, **kw):
        n[0] += 1
        return {"id": f"S{n[0]}", "material_id": mid, "extra_material_refs": refs, "visible": True, "volume": 1.0,
                "speed": 1.0, "target_timerange": {"start": 0, "duration": dur},
                "source_timerange": {"start": 0, "duration": dur},
                "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}}, **kw}

    def text(t, size, color):
        return json.dumps({"text": t, "styles": [{"range": [0, len(t)], "size": size,
                                                  "fill": {"content": {"solid": {"color": color}}}}]}, ensure_ascii=False)

    def anim(*a):
        return mat("material_animations", animations=list(a))

    def fade():
        return mat("audio_fades", fade_in_duration=0, fade_out_duration=0)

    def audio(name, path):
        return seg(mat("audios", name=name, path=path, duration=1_000_000, wave_points=[], music_id="",
                       local_material_id=""), [fade()])

    fade_out = {"type": "out", "start": 0, "duration": 100000, "name": "Fade"}
    tracks = [("video", [seg(mat("videos", path="", material_name="", duration=0, width=1080, height=1080), [anim()])]),
              ("video", [seg(mat("videos", path="", material_name="bars", duration=60_000_000, width=1080, height=1920), [],
                             dur=60_000_000, visible=False)]),
              ("text", [seg(mat("texts", content=text("제목", 25.1, [1, 1, 1])), [anim()])]),
              ("sticker", [seg(mat("stickers", name="sticker"), [])]),
              ("text", [seg(mat("texts", content=text("자막", 14, [1, 1, 1])), [anim(dict(fade_out))], visible=False),
                        seg(mat("texts", content=text("강조", 16, [1, 0.25, 0.25])), [anim()], visible=False)]),
              ("audio", [audio("file_sfx.wav", sfx["file"]), audio("Compound demo", sfx["flash"])]),
              ("audio", [audio("bgm.wav", sfx["bgm"])]),
              ("audio", [])]
    info = {"id": "TEMPLATE", "name": "template", "duration": 0, "materials": mats,
            "tracks": [{"id": f"T{i}", "type": t, "name": "", "attribute": 0, "flag": 0, "segments": s}
                       for i, (t, s) in enumerate(tracks)]}
    dump(os.path.join(d, "draft_info.json"), info)
    dump(os.path.join(d, "draft_meta_info.json"), {"draft_materials": []})
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    root = tempfile.mkdtemp(prefix="shorts_demo_")
    ep = os.path.join(root, "episode")
    os.makedirs(ep)
    ass = os.path.join(root, "assets")
    os.makedirs(ass)
    drafts = os.path.join(root, "drafts")
    os.makedirs(drafts)
    sfx = {k: os.path.join(ass, f"{k}.wav") for k in ("file", "flash", "bgm")}
    for f in sfx.values():
        tone(f, 3.0, 660)
    dump(os.path.join(ass, "flash_out_anim.json"), {"type": "out", "start": 0, "duration": 233333, "name": "Flash Out"})
    dump(os.path.join(ass, "zoom_in_anim.json"), {"type": "in", "start": 0, "duration": 500000, "name": "Zoom In"})
    source(ep)
    narration(ep)
    storyboard(ep)
    tpl = template(root, sfx)
    cfg = os.path.join(root, "config.json")
    dump(cfg, {"font_path": a.font, "capcut_root": drafts, "capcut_template_dir": tpl, "capcut_assets_dir": ass,
               "capcut_file_sfx_name": "file_sfx.wav", "sfx_dirs": [ass], "flash_sfx": sfx["flash"],
               "transition_sfx_prefixes": [], "episode_roots": [root], "elevenlabs_voice_id": "",
               "elevenlabs_key_file": ""})
    env = {**os.environ, "SHORTS_CONFIG": cfg, "SHORTS_CAPCUT_ROOT": drafts}
    env.pop("ELEVENLABS_API_KEY", None)

    results = []

    def step(name, *cmd, expect_fail=False, must=()):
        script = os.path.join(SCRIPTS, cmd[0])
        r = subprocess.run([sys.executable, script, *cmd[1:]], env=env, capture_output=True, text=True, cwd=root)
        ok = (r.returncode != 0) if expect_fail else (r.returncode == 0)
        missing = [m for m in must if not os.path.exists(os.path.join(ep, m))]
        ok = ok and not missing
        results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  없는 파일: {missing}" if missing else ""))
        if not ok:
            print((r.stdout + r.stderr)[-1500:])
        return r

    step("스토리보드 → 파일", "from_storyboard.py", ep, must=["card.json", "cuts.json", "subs_plan.json"])
    r = step("나레이션 재사용(API 호출 없음)", "narrate.py", ep)
    results.append(("모든 줄이 재사용됨", r.stdout.count("(그대로)") == len(NARR)))
    print(f"{'PASS' if results[-1][1] else 'FAIL'}  모든 줄이 재사용됨")
    step("렌더", "render_edit.py", ep, must=["edit.mp4", "narration.wav", "preview.mp4", "timeline.json"])
    step("자막", "make_subs.py", ep, must=["subs.json", "edit.ko.srt", "edit.en.srt"])
    step("싱크 맞춤", "sync_subs.py", ep, must=["work/sync_report.md"])
    step("검수", "qa.py", ep, must=["qa_report.md"])
    step("대본 내보내기", "apply_script.py", ep, "--export", must=["대본.txt"])
    step("CapCut draft 쓰기(임시 폴더)", "capcut_export.py", ep, "demo", must=["컷번호.md", "cuts/01_01.mov"])
    step("draft 부분 수정", "capcut_export.py", ep, "demo", "--update", "subs,title")
    step("버전 목록", "versions.py", ep, "list")
    step("검토용 mp4", "render_final.py", ep, "demo")

    drafts_now = sorted(os.listdir(drafts))
    d = json.load(open(os.path.join(drafts, drafts_now[-1], "draft_info.json")))
    bad = []
    for kind in ("videos", "audios"):
        for m in d["materials"][kind]:
            p = m.get("path")
            if p and (not os.path.isabs(p) or not os.path.exists(p)):
                bad.append(p)
    results.append(("draft 미디어 경로가 절대경로이고 파일이 있음", not bad))
    print(f"{'PASS' if not bad else 'FAIL'}  draft 미디어 경로 {bad[:3] if bad else ''}")
    exported = [f for f in os.listdir(os.path.join(ep, "export"))] if os.path.isdir(os.path.join(ep, "export")) else []
    results.append(("검토용 mp4 생성", bool(exported)))
    print(f"{'PASS' if exported else 'FAIL'}  검토용 mp4 생성 {exported}")

    # 새 문구는 API 호출 전에 설정 오류로 멈춰야 한다.
    card_p = os.path.join(ep, "card.json")
    card = json.load(open(card_p))
    next(x for x in card["script"] if x["type"] == "narr")["text"] = "새로 녹음이 필요한 가상 문구"
    dump(card_p, card)
    r = step("voice_id 없으면 API 호출 전 중단", "narrate.py", ep, expect_fail=True)
    results.append(("중단 메시지 확인", "elevenlabs_voice_id" in (r.stderr + r.stdout)))
    print(f"{'PASS' if results[-1][1] else 'FAIL'}  중단 메시지 확인")

    fails = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(fails)}/{len(results)} 통과")
    if a.keep:
        print("결과 폴더:", root)
    else:
        shutil.rmtree(root)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
