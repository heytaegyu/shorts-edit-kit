"""회차 산출물 → CapCut draft. 회차당 draft는 하나 — 고칠 때는 그 draft를 그 자리에서 수정.

사용:
  python scripts/capcut_export.py <회차> <draft 이름>                      # 새로 만들기(설정 capcut_template_dir 복제)
  python scripts/capcut_export.py <회차> <draft 이름> --update [부분,...] [--from 다른 draft] [--relaunch]
    같은 draft를 고침. 고칠 때마다 결과를 <회차>/work/capcut_versions/<draft>/vNN/ 에 버전으로 저장
    (CapCut 목록에 파일이 늘지 않게). --restore NN 으로 되돌림.
    부분: cuts(편집본을 다시 렌더했을 때 영상 컷 다시 나누기), title, subs, video, narr, sfx, mix, order,
    media (생략하면 cuts 빼고 전부). 고른 부분만 다시 채우고 스티커·강조 문구 등 다른 트랙은 그대로.
    --from: 다른 draft의 내용을 가져와 이 draft에 씀(이름 정리용).
    --relaunch: CapCut이 켜져 있으면 종료 → 쓰기 → 다시 실행(사용자가 허락한 경우만).
⚠ CapCut이 실행 중이면 쓰지 않는다(종료 시 draft 목록을 덮어씀).

트랙 순서(위→아래): 영상, 프레임, 제목, 스티커, 자막, 나레이션자막, 강조 / 나레이션, 효과음1·2…, BGM
- 영상: edit.mp4를 샷마다 분할한 세그먼트(통짜 금지). 볼륨 = 대사를 나레이션과 같은 크기로
- 전환: card.json flash_before_blocks 블록 앞 컷 — 나가는 샷 끝 0.23초 Flash Out, 컴파운드
  효과음(Riser + 셔터)은 셔터 「찰칵」이 Flash Out 시작에 오게
- 제목 2줄(1줄 흰색 / 2줄 노랑). 후킹 우선 — 넘치는 줄만 글자 크기를 줄임
- 자막: subs.json → 템플릿 자막 프리셋, 키워드 색(range)·크기·애니메이션(Zoom In / 페이드 / 없음), 보이게.
  나레이션 자막은 「나레이션자막」 트랙(대사 자막 「자막」 트랙 위)으로 따로
- 나레이션: 줄마다 NN.mp3 세그먼트(영상처럼 컷 단위)
- 효과음: card.json sfx_cues [기준, 이름(앞부분)(, 오프셋, 길이, 볼륨)] — 기준은 초 / "sub:자막 글" / "block:N".
  템플릿 효과음 풀 → 없으면 설정 sfx_dirs 에서 파일로. 휙(전환)은 가장 큰 지점을 컷에 맞춤
- 음량 비율: 대사 = 나레이션(LUFS), BGM = 나레이션 − 8 LU, 효과음 순간 최대 = 나레이션 순간 최대 − 3 dB
- 미디어(media): edit.mp4 · 원본(source/*.mp4, original.mp4)을 CapCut 미디어 목록에 넣어 둠(가끔 컷을 더 쓸 때).
  템플릿에서 따라온 다른 회차 파일은 목록에서 뺌
"""
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid

from PIL import ImageFont

import kit_config

ROOT = os.environ.get("SHORTS_CAPCUT_ROOT", kit_config.path("capcut_root"))
TEMPLATE_DIR = kit_config.path("capcut_template_dir")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = kit_config.path("capcut_assets_dir")
SFX_DIRS = kit_config.paths("sfx_dirs")
US = 1_000_000
FPS = 30
YELLOW = [1.0, 0.86, 0.0]  # 제목 2줄(템플릿 색)
WHITE = [1.0, 1.0, 1.0]
COLORS = {"yellow": [1.0, 0.875, 0.361],  # #ffdf5c 기본 강조
          "green": [0.231, 1.0, 0.302],   # #3bff4d 센 리액션·원화
          "red": [1.0, 0.251, 0.251]}     # #ff4040 센 리액션
BGM_UNDER = 8.0     # BGM은 나레이션보다 8 LU 작게
SFX_UNDER = 3.0     # 효과음 순간 최대는 나레이션 순간 최대보다 3 dB 작게
SHUTTER_AT = 1.003  # 컴파운드 효과음 세그먼트 시작 → 셔터 「찰칵」(템플릿 실측)
FLASH_SFX = kit_config.path("flash_sfx")
TRANSITION_SFX = tuple(kit_config.get("transition_sfx_prefixes"))  # 가장 큰 지점을 기준 시각(컷)에 맞추는 효과음
ROLES = ["영상", "후보컷", "후보", "프레임", "제목", "스티커", "자막", "나레이션자막", "강조", "나레이션", "효과음1",
         "효과음2", "BGM"]
TEMPLATE_ROLES = ["영상", "프레임", "제목", "스티커", "자막", "효과음1", "BGM", "효과음2"]
V2_ROLES = ["영상", "프레임", "제목", "스티커", "자막", "강조", "효과음1", "BGM", "효과음2", "나레이션"]


def nid():
    return str(uuid.uuid4()).upper()


def us(t):
    return int(round(t * US))


def capcut_running():
    return subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0


def probe_dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", path], capture_output=True, text=True).stdout)


_loud = {}


def loudness(path):
    """(통합 LUFS, 순간 최대 LUFS)"""
    if path not in _loud:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128",
                              "-f", "null", "-"], capture_output=True, text=True).stderr
        i = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        m = [float(x) for x in re.findall(r"M:\s*(-?[\d.]+)", err)]
        _loud[path] = (float(i[-1]) if i else -70.0, max(m) if m else -70.0)
    return _loud[path]


def db_gain(db, lo=0.03, hi=3.0):
    return min(hi, max(lo, 10 ** (db / 20)))


class Draft:
    def __init__(self, info):
        self.d = info
        self.m = info["materials"]
        self.where = {}
        for k, v in self.m.items():
            if isinstance(v, list):
                for o in v:
                    if isinstance(o, dict) and "id" in o:
                        self.where[o["id"]] = k

    def mat(self, mid):
        return next(o for o in self.m[self.where[mid]] if o["id"] == mid)

    def import_mat(self, src, mid):
        k = src.where[mid]
        o = copy.deepcopy(src.mat(mid))
        o["id"] = nid()
        self.m.setdefault(k, []).append(o)
        self.where[o["id"]] = k
        return o

    def import_seg(self, src, seg):
        """src draft의 세그먼트를 재료째 복제해 이 draft로(src가 자기 자신이면 복제)."""
        s = copy.deepcopy(seg)
        s["id"] = nid()
        s["material_id"] = self.import_mat(src, seg["material_id"])["id"]
        s["extra_material_refs"] = [self.import_mat(src, r)["id"] for r in seg["extra_material_refs"]]
        return s

    def ref(self, seg, kind):
        return next((self.mat(r) for r in seg["extra_material_refs"] if self.where.get(r) == kind),
                    None)


def write_cut_map(ep, tl, subs, names):
    """컷번호.md — CapCut 영상 클립 이름(블록.샷)과 같은 번호로 시간·원본 구간·그 컷의 자막을 적어 둠."""
    rows, k = [], 0
    for bi, b in enumerate(tl["blocks"]):
        for sh in b["shots"]:
            a, e = sh["start"], sh["end"]
            txt = " / ".join(x["ko"].replace("\n", " ") for x in subs if a - 0.02 <= x["start"] < e - 0.02)
            src = sh.get("src")
            rows.append(f"| {names[k]} | {a:.2f}~{e:.2f} | {'나레이션' if b['kind'] == 'narr' else '원본 대사'} | "
                        f"{'%.2f~%.2f' % tuple(src) if src else ''} | {txt} |")
            k += 1
    head = ("# 컷 번호\n\nCapCut 영상 클립 이름과 같은 번호(씬_컷)입니다. 고칠 때 「03_02 인물 짤림」처럼 말해 주세요.\n\n"
            "| 번호 | 시간(초) | 종류 | 원본 구간 | 자막 |\n|---|---|---|---|---|\n")
    open(os.path.join(ep, "컷번호.md"), "w").write(head + "\n".join(rows) + "\n")


def cut_names(ep, blocks):
    """클립 번호. 스토리보드에 scene이 있으면 씬_컷(03_02), 없으면 예전 회차처럼 블록_샷."""
    sb, rows = os.path.join(ep, "work", "storyboard"), []
    data = os.path.join(sb, "data.json")
    if os.path.exists(data):
        rows = json.load(open(data)).get("rows", [])
    else:
        for f in sorted(glob.glob(os.path.join(sb, "rows", "*.json"))):
            r = json.load(open(f))
            r.setdefault("id", os.path.basename(f)[:-5])
            rows.append(r)
    scene = {r["id"]: r["scene"] for r in rows if r.get("scene") is not None}
    names, count, last = [], {}, 0
    for bi, b in enumerate(blocks):
        rid = b.get("row") or (b.get("rows") or [None])[0]
        if scene:
            sc = scene.get(rid, last)  # scene이 빠진 장면은 앞 씬에 붙임
            last = sc
        else:
            sc, count = bi + 1, {}
        for _ in b["shots"]:
            count[sc] = count.get(sc, 0) + 1
            names.append(f"{sc:02d}_{count[sc]:02d}")
    return names


def set_range(seg, start_us, dur_us, src_us=None):
    seg["target_timerange"] = {"start": start_us, "duration": dur_us}
    if seg.get("source_timerange") is not None:
        seg["source_timerange"] = {"start": src_us or 0, "duration": dur_us}


def set_text(mat, text, colored, size=None):
    """colored: [(start, end, rgb)] — 나머지는 흰색. size: 글자 크기(없으면 프리셋)."""
    c = json.loads(mat["content"])
    base = copy.deepcopy(c["styles"][0])
    base["fill"]["content"]["solid"]["color"] = WHITE
    if size:
        base["size"] = size
    styles, pos = [], 0
    for a, b, rgb in sorted(colored) + [(len(text), len(text), None)]:
        if a > pos:
            st = copy.deepcopy(base); st["range"] = [pos, a]; styles.append(st)
        if rgb is not None and b > a:
            st = copy.deepcopy(base); st["range"] = [a, b]
            st["fill"]["content"]["solid"]["color"] = rgb
            styles.append(st)
        pos = max(pos, b)
    c["text"], c["styles"] = text, styles
    mat["content"] = json.dumps(c, ensure_ascii=False, separators=(",", ":"))


def kw_ranges(text, kws):
    """kws: [[단어, 색 이름]] → [(시작, 끝, rgb)] (겹치면 앞 것 우선)"""
    out = []
    for k, c in kws:
        i = text.find(k)
        if i >= 0 and not any(a < i + len(k) and i < b for a, b, _ in out):
            out.append((i, i + len(k), COLORS[c]))
    return out


def find_sfx_file(key):
    for d in SFX_DIRS:
        for p in sorted(glob.glob(os.path.join(glob.escape(d), "**", "*"), recursive=True)):
            base = unicodedata.normalize("NFC", os.path.basename(p))  # 맥 파일 이름은 NFD
            if base.startswith(unicodedata.normalize("NFC", key)) and p.lower().endswith(
                    (".mp3", ".wav", ".m4a", ".aac")):
                return p
    return None


class Builder:
    def __init__(self, ep, dr, tpl, roles):
        self.ep, self.dr, self.tpl = ep, dr, tpl
        self.card = json.load(open(os.path.join(ep, "card.json")))
        self.tl = json.load(open(os.path.join(ep, "timeline.json")))
        self.subs = json.load(open(os.path.join(ep, "subs.json")))["events"]
        self.narr = json.load(open(os.path.join(ep, "narration.json")))["lines"]
        self.edit = os.path.join(ep, "edit.mp4")
        self.D = probe_dur(self.edit)
        self.Dus = us(self.D)
        self.track = dict(zip(roles, dr.d["tracks"]))
        for r, t in self.track.items():
            t["name"] = r
        tt = dict(zip(TEMPLATE_ROLES, tpl.d["tracks"]))  # 템플릿 프리셋
        self.tpl_track = tt
        self.sub_preset, self.red_preset = tt["자막"]["segments"][:2]
        self.pool = {}
        for r in ("효과음1", "효과음2"):
            for s in tt[r]["segments"]:
                self.pool.setdefault(tpl.mat(s["material_id"])["name"], s)
        self.file_sfx = self.pool[kit_config.require("capcut_file_sfx_name")]  # 파일 효과음·나레이션 세그먼트 견본(로컬 파일)
        self.narr_loud = loudness(os.path.join(ep, "narration.wav"))
        if not self.narr_loud[0] > -40:  # 나레이션 없는 회차: 대사(편집본)를 기준으로
            il, pk = loudness(self.edit)
            self.narr_loud = (-18.5, pk + (-18.5 - il))

    def new_track(self, role, like):
        t = {**copy.deepcopy(self.tpl_track[like]), "id": nid(), "segments": [], "name": role,
             "attribute": 0}
        self.dr.d["tracks"].append(t)
        self.track[role] = t
        return t

    # ── 영상 ──
    def video_split(self):
        """편집본을 샷마다 분할(새로 만들 때, 또는 다시 렌더한 뒤 cuts), 검은 바 컴파운드를 길이만큼."""
        dr, t = self.dr, self.track["영상"]
        seg = t["segments"][0]
        dr.mat(seg["material_id"]).update(path=self.edit, material_name="edit.mp4",
                                          duration=self.Dus, width=1080, height=1080)
        shots = [(s["start"], s["end"]) for b in self.tl["blocks"] for s in b["shots"]]
        # 컷마다 「블록_샷」 번호(01_02)를 CapCut 클립 이름으로 — 소통용. 컷번호.md에도 같은 번호.
        names = cut_names(self.ep, self.tl["blocks"])
        write_cut_map(self.ep, self.tl, self.subs, names)
        # 컷마다 파일을 따로 만들어 파일 이름 자체를 컷 번호로(<회차>/cuts/03_02.mov)
        bounds = [(us(round(a * FPS) / FPS), us(round(b * FPS) / FPS) if k < len(shots) - 1 else self.Dus)
                  for k, (a, b) in enumerate(shots)]
        files = self.cut_files(bounds, names)
        segs = []
        for k, (a, b) in enumerate(bounds):
            s = seg if k == 0 else dr.import_seg(dr, seg)
            dr.mat(s["material_id"]).update(path=files[k], material_name=os.path.basename(files[k]),
                                            duration=max(b - a, us(probe_dur(files[k]))))
            set_range(s, a, b - a, 0)  # 경계를 정수 µs로 딱 맞춤, 파일은 0초부터
            segs.append(s)
        t["segments"] = segs
        self.cut_bounds, self.cut_label = bounds, names
        bars = self.track["프레임"]
        bar = bars["segments"][0]
        blen = bar["source_timerange"]["duration"]
        segs, x = [], 0
        while x < self.Dus:
            s = bar if not segs else dr.import_seg(dr, bar)
            set_range(s, x, min(blen, self.Dus - x))
            segs.append(s); x += blen
        bars["segments"] = segs

    def cut_files(self, bounds, names):
        """편집본을 컷마다 잘라 <회차>/cuts/<씬_컷>.mov (영상 + 대사 PCM, 경계에서 소리가 끊기지 않게)."""
        d = os.path.join(os.path.abspath(self.ep), "cuts")
        os.makedirs(d, exist_ok=True)
        out = []
        for (a, b), n in zip(bounds, names):
            p = os.path.join(d, f"{n}.mov")
            f0, f1 = round(a / US * FPS), round(b / US * FPS)  # 프레임·샘플 번호로 잘라야 1프레임도 안 빠짐
            s0, s1 = round(a / US * 48000), round(b / US * 48000)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", self.edit, "-filter_complex",
                            f"[0:v]trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS[v];"
                            f"[0:a]aresample=48000,atrim=start_sample={s0}:end_sample={s1},asetpts=PTS-STARTPTS[a]",
                            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
                            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", p], check=True)
            out.append(p)
        for f in os.listdir(d):  # 지난 버전의 남은 컷 파일 정리
            if f.endswith(".mov") and os.path.join(d, f) not in out:
                os.remove(os.path.join(d, f))
        return out

    def alt_cuts(self):
        """컷마다 후보 컷 1개를 그 컷 바로 위 「후보컷」 트랙에 숨김(CapCut V)으로.
        후보 = 원본에서 그 컷의 카메라 장면 바로 다음(없으면 바로 앞) 장면, 편집본에 안 쓴 구간, 같은 길이."""
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import render_edit as R
        cuts = json.load(open(os.path.join(self.ep, "cuts.json")))
        src, rect = os.path.join(self.ep, cuts["source"]), cuts["content_rect"]
        shots = [sh for b in self.tl["blocks"] for sh in b["shots"]]
        used = [sh["src"] for sh in shots]
        d = os.path.join(os.path.abspath(self.ep), "cuts")
        for f in glob.glob(os.path.join(glob.escape(d), "후보_*.mp4")):
            os.remove(f)
        t = self.track.get("후보컷")
        if t is None:
            t = {**copy.deepcopy(self.track["영상"]), "id": nid(), "segments": [], "name": "후보컷",
                 "attribute": 0, "flag": 0}
            self.dr.d["tracks"].insert(self.dr.d["tracks"].index(self.track["영상"]) + 1, t)
            self.track["후보컷"] = t
        base = self.track["영상"]["segments"][0]
        segs, made = [], 0
        for sh, (a_us, b_us), n in zip(shots, self.cut_bounds, self.cut_label):
            dur = (b_us - a_us) / US
            a, b = sh["src"]
            zones = R.source_cuts(src, rect, max(0.0, a - 12), b + 12)
            edges = [(max(0.0, a - 12), max(0.0, a - 12))] + zones + [(b + 12, b + 12)]
            scenes = [(z0[1], z1[0]) for z0, z1 in zip(edges, edges[1:]) if z1[0] - z0[1] >= dur + 0.2]
            own = R.scene_of(zones, a, b)
            after = sorted((x for x in scenes if x[0] >= own[1] - 0.05), key=lambda x: x[0])
            before = sorted((x for x in scenes if x[1] <= own[0] + 0.05), key=lambda x: -x[1])
            pick = None
            for x0, x1 in after + before:
                c0 = x0 + 0.1 if x0 >= own[1] - 0.05 else x1 - 0.1 - dur
                if not any(min(c0 + dur, u1) - max(c0, u0) > 0 for u0, u1 in used):
                    pick = c0
                    break
            if pick is None:
                continue
            p = os.path.join(d, f"후보_{n}.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{pick:.3f}", "-i", src,
                            "-frames:v", str(round(dur * FPS)), "-vf",
                            R.crop_filter(rect, {"cx": 0.5, "zoom": 1.0, "flip": False}), "-an",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", p],
                           check=True)
            s = self.dr.import_seg(self.dr, base)
            self.dr.mat(s["material_id"]).update(path=p, material_name=os.path.basename(p),
                                                 duration=max(b_us - a_us, us(probe_dur(p))))
            an = self.dr.ref(s, "material_animations")
            if an:
                an["animations"] = []
            s["volume"], s["visible"] = 0.0, False  # 숨김 + 무음: 최종 내보내기에 안 나옴
            set_range(s, a_us, b_us - a_us, 0)
            segs.append(s)
            made += 1
        t["segments"] = segs
        return made

    def video_flash(self):
        """기존 Flash Out을 지우고 flash_before_blocks 컷 앞 샷 끝에 다시 넣음. → 컷 시각(µs)"""
        anim = json.load(open(os.path.join(ASSETS, "flash_out_anim.json")))
        segs = self.track["영상"]["segments"]
        for s in segs:
            a = self.dr.ref(s, "material_animations")
            if a:
                a["animations"] = [x for x in a["animations"] if x["name"] != "Flash Out"]
        cuts = []
        for bi in self.card.get("flash_before_blocks", []):
            cut = us(round(self.tl["blocks"][bi]["start"] * FPS) / FPS)
            s = min(segs, key=lambda s: abs(sum(s["target_timerange"].values()) - cut))
            dur = s["target_timerange"]["duration"]
            self.dr.ref(s, "material_animations")["animations"].append(
                {**anim, "start": dur - anim["duration"]})
            cuts.append(sum(s["target_timerange"].values()))
        return cuts

    # ── 제목 ──
    def title(self):
        """2줄 제목. 후킹 문구 우선 — 줄이 화면 폭(8자 + 띄어쓰기 3개)을 넘으면 그 줄만 글자 크기를 줄임."""
        seg = self.track["제목"]["segments"][0]
        mat = self.dr.mat(seg["material_id"])
        l1, l2 = self.card["title_screen"]
        text = f"{l1}\n{l2}"
        f = ImageFont.truetype(kit_config.font(), 100)
        limit = f.getlength("가나다 라마 바사 아")  # 사용자 실측: 한 줄 최대 8자 + 띄어쓰기 3개
        tpl_mat = self.tpl.mat(self.tpl_track["제목"]["segments"][0]["material_id"])
        size = json.loads(tpl_mat["content"])["styles"][0]["size"]
        set_text(mat, text, [(len(l1) + 1, len(text), YELLOW)], size)
        c = json.loads(mat["content"])
        for k, (line, a) in enumerate([(l1, 0), (l2, len(l1) + 1)]):
            over = f.getlength(line) / limit
            if over > 1:
                print(f"제목 {k + 1}줄 {over:.2f}배 넘침 → 글자 크기 {size / over:.1f}")
                for st in c["styles"]:
                    if a <= st["range"][0] < a + len(line) + 1:
                        st["size"] = size / over
        mat["content"] = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        set_range(seg, 0, self.Dus)

    def sticker_emph(self):
        """새로 만들 때만: 점선 원 스티커 위치, 강조 문구 트랙(빨강 프리셋)."""
        seg = self.track["스티커"]["segments"][0]
        x, y = self.card.get("sticker_xy", [seg["clip"]["transform"]["x"],
                                            seg["clip"]["transform"]["y"]])
        seg["clip"]["transform"] = {"x": x, "y": y}
        t = self.new_track("강조", "자막")
        emph = self.card.get("emphasis")
        if emph:
            s = self.dr.import_seg(self.tpl, self.red_preset)
            m = self.dr.mat(s["material_id"])
            set_text(m, emph["text"], [(0, len(emph["text"]), COLORS["red"])])
            set_range(s, us(emph.get("start", 0)), us(emph.get("dur", 0.6)))
            s["clip"]["transform"] = {"x": emph.get("x", 0.0), "y": emph.get("y", 0.0)}
            s["visible"] = True
            t["segments"] = [s]

    # ── 자막 ──
    def subtitles(self):
        zoom = json.load(open(os.path.join(ASSETS, "zoom_in_anim.json")))
        out = {"narr": [], "orig": []}
        for e in self.subs:
            s = self.dr.import_seg(self.tpl, self.sub_preset)
            set_text(self.dr.mat(s["material_id"]), e["ko"], kw_ranges(e["ko"], e["kw"]),
                     e.get("size"))
            a, b = us(e["start"]), us(e["end"])
            set_range(s, a, b - a)
            s["visible"] = True  # 템플릿 프리셋은 숨김 상태라 켜 줌
            am = self.dr.ref(s, "material_animations")
            an = e.get("anim", {"in": "fade"})
            if an["in"] == "zoom":
                am["animations"] = [{**zoom, "start": 0, "duration": us(an["dur"])}]
            elif an["in"] is None:
                am["animations"] = []
            else:  # 프리셋 페이드: 나가기를 세그먼트 끝에 맞춤
                for x in am["animations"]:
                    if x["type"] == "out":
                        x["start"] = max(0, (b - a) - x["duration"])
            out["narr" if e["kind"] == "narr" else "orig"].append(s)
        self.track["자막"]["segments"] = out["orig"]
        t = self.track.get("나레이션자막") or self.new_track("나레이션자막", "자막")
        t["segments"] = out["narr"]
        return len(self.subs)

    # ── 나레이션: 줄마다 세그먼트 ──
    def narration(self):
        t = self.track.get("나레이션") or self.new_track("나레이션", "효과음1")
        lines = {l["i"]: l for l in self.narr}
        segs = []
        for b in self.tl["blocks"]:
            if b["kind"] != "narr":
                continue
            line = lines[b["line"]]
            ns = us(b["narr_start"])  # 파일 0초의 편집본 시각(앞 무음만큼 음수일 수 있음)
            start = max(us(b["start"]), ns)  # 앞 무음이 블록 앞으로 나오면 잘라냄(겹침 금지)
            src = start - ns
            end = min(us(b["end"]), ns + us(line["dur"]))
            s = self.dr.import_seg(self.tpl, self.file_sfx)
            self.dr.mat(s["material_id"]).update(
                path=os.path.join(HERE, line["file"]), name=f"나레이션 {b['line']:02d}", duration=us(line["dur"]),
                wave_points=[], music_id="", local_material_id="")
            fade = self.dr.ref(s, "audio_fades")
            if fade:
                fade.update(fade_in_duration=0, fade_out_duration=0)
            s["volume"] = 1.0
            s["target_timerange"] = {"start": start, "duration": end - start}
            s["source_timerange"] = {"start": src, "duration": end - start}
            segs.append(s)
        t["segments"] = segs
        return len(segs)

    # ── 효과음 ──
    def sfx_seg(self, key):
        src = next((v for k, v in self.pool.items() if k.startswith(key)), None)
        if src is not None:
            s = self.dr.import_seg(self.tpl, src)
            m = self.dr.mat(s["material_id"])
            path = FLASH_SFX if m["name"].startswith("Compound") else m["path"]
        else:
            path = find_sfx_file(key)
            if not path:
                return None
            s = self.dr.import_seg(self.tpl, self.file_sfx)
            dur = us(probe_dur(path))
            self.dr.mat(s["material_id"]).update(path=path, name=os.path.basename(path),
                                                 duration=dur, wave_points=[], music_id="",
                                                 local_material_id="")
            d = min(dur, us(3.0))
            s["source_timerange"] = {"start": 0, "duration": d}
            s["target_timerange"] = {"start": 0, "duration": d}
        s["volume"] = db_gain(self.narr_loud[1] - SFX_UNDER - loudness(path)[1], hi=1.5)
        return s

    def peak_offset(self, s):
        """세그먼트 시작 → 가장 큰 소리까지(µs)."""
        import numpy as np
        m = self.dr.mat(s["material_id"])
        a, d = s["source_timerange"]["start"] / US, s["source_timerange"]["duration"] / US
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i",
                              m["path"], "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                             capture_output=True).stdout
        x = np.frombuffer(raw, np.float32)
        n = len(x) // 480
        return us(int(np.argmax((x[:n * 480] ** 2).reshape(n, 480).mean(1))) * 0.01)

    def anchor(self, a):
        if isinstance(a, (int, float)):
            return float(a)
        kind, _, val = a.partition(":")
        if kind == "sub":
            return next(e["start"] for e in self.subs if val in e["ko"])
        if kind == "block":
            return self.tl["blocks"][int(val)]["start"]
        raise ValueError(a)

    def sfx(self, flash_cuts):
        placed = []
        for cue in self.card.get("sfx_cues", []):
            try:
                at = self.anchor(cue[0])
            except StopIteration:
                print(f"⚠ 효과음 기준 자막을 못 찾음(건너뜀): {cue[0]} — card.json sfx_cues를 고칠 것"); continue
            s = self.sfx_seg(cue[1])
            if s is None:
                print("효과음 없음(건너뜀):", cue[1]); continue
            t0 = us(at + (cue[2] if len(cue) > 2 else 0.0))
            if cue[1].startswith(TRANSITION_SFX):
                t0 -= self.peak_offset(s)
            if len(cue) > 3 and cue[3]:  # 길이 지정(사용자가 CapCut에서 줄인 길이 반영)
                d = min(us(cue[3]), s["target_timerange"]["duration"])
                s["target_timerange"]["duration"] = d
                s["source_timerange"]["duration"] = d
            if len(cue) > 4 and cue[4] is not None:  # 볼륨 지정(사용자가 정한 값 우선)
                s["volume"] = cue[4]
            placed.append((max(0, t0), s))
        for cut in flash_cuts:  # 셔터 「찰칵」 = Flash Out 시작(컷 − 0.23초)
            placed.append((max(0, cut - 233333 - us(SHUTTER_AT)), self.sfx_seg("Compound")))
        lanes = []
        for t0, s in sorted(placed, key=lambda x: x[0]):
            if t0 >= self.Dus - us(0.1):
                print(f"⚠ 영상 끝을 넘는 효과음(건너뜀): {t0 / US:.2f}초")
                continue
            d = min(s["target_timerange"]["duration"], self.Dus - t0)
            s["target_timerange"] = {"start": t0, "duration": d}
            s["source_timerange"]["duration"] = d
            lane = next((l for l in lanes if sum(l[-1]["target_timerange"].values()) <= t0), None)
            if lane is None:
                lane = []; lanes.append(lane)
            lane.append(s)
        for k in range(max(2, len(lanes))):  # 겹치면 트랙을 늘림(한 트랙 안 겹침 금지)
            role = f"효과음{k + 1}"
            t = self.track.get(role) or self.new_track(role, "효과음1")
            t["segments"] = lanes[k] if k < len(lanes) else []
        return len(placed)

    # ── 후보 영상: card.json "extras" 파일들을 타임라인 끝(영상 끝 + 1초) 「후보」 트랙에 ──
    def extras(self):
        files = [os.path.join(self.ep, f) for f in self.card.get("extras", [])]
        files = [f for f in files if os.path.exists(f)]
        t = self.track.get("후보")
        if t is None:
            t = {**copy.deepcopy(self.track["영상"]), "id": nid(), "segments": [], "name": "후보",
                 "attribute": 0, "flag": 0}
            self.dr.d["tracks"].insert(self.dr.d["tracks"].index(self.track["영상"]) + 1, t)
            self.track["후보"] = t
        base = self.track["영상"]["segments"][0]
        segs, x = [], self.Dus + US
        for f in files:
            s = self.dr.import_seg(self.dr, base)
            d = us(probe_dur(f))
            self.dr.mat(s["material_id"]).update(path=f, material_name=os.path.basename(f),
                                                 duration=d, width=1080, height=1080)
            a = self.dr.ref(s, "material_animations")
            if a:
                a["animations"] = []
            s["volume"] = 0.0
            set_range(s, x, d, 0)
            segs.append(s); x += d + us(0.5)
        t["segments"] = segs
        return len(segs)

    # ── 음량 ──
    def mix(self):
        n_i = self.narr_loud[0]
        g = db_gain(n_i - loudness(self.edit)[0], hi=6.0)
        for s in self.track["영상"]["segments"]:
            s["volume"] = g
        bgm = self.track["BGM"]["segments"]
        for s in bgm:
            s["volume"] = db_gain(n_i - BGM_UNDER - loudness(self.dr.mat(s["material_id"])["path"])[0])
        if len(bgm) == 1:
            set_range(bgm[0], 0, self.Dus, bgm[0]["source_timerange"]["start"])
        print(f"음량: 나레이션 {n_i:.1f} LUFS 기준 — 대사 ×{g:.2f}" +
              (f", BGM ×{bgm[0]['volume']:.3f}" if bgm else ""))

    # ── 트랙 순서·켜기 ──
    def order(self):
        d = self.dr.d
        visual = [self.track[r] for r in ROLES[:ROLES.index("강조") + 1] if r in self.track]
        sfx = sorted([t for r, t in self.track.items() if r.startswith("효과음")],
                     key=lambda t: int(t["name"][3:]))
        audio = [self.track[r] for r in ("나레이션",) if r in self.track] + sfx + \
            [self.track[r] for r in ("BGM",) if r in self.track]
        others = [t for t in d["tracks"] if t not in visual and t not in audio]
        d["tracks"] = visual + others + audio
        for i, t in enumerate(d["tracks"]):
            if t["type"] == "audio":
                t["attribute"] = 0  # 템플릿 효과음2 트랙이 음소거라 해제
            for s in t["segments"]:
                s["track_render_index"] = i
                if t.get("name") in ("제목", "스티커", "자막", "나레이션자막", "강조"):
                    s["visible"] = True


def save(dst, d, name, new):
    info_path = os.path.join(dst, "draft_info.json")
    now = int(time.time() * US)
    d["duration"] = max(sum(s["target_timerange"].values()) for t in d["tracks"]
                        for s in t["segments"] if t["type"] == "video")
    d["update_time"] = now
    tdir = os.path.join(dst, "Timelines")
    if new:
        old_tid = d["id"]
        d["id"] = nid()
        d["name"] = name
        if os.path.isdir(os.path.join(tdir, old_tid)):
            os.rename(os.path.join(tdir, old_tid), os.path.join(tdir, d["id"]))
            pj = os.path.join(tdir, "project.json")
            p = json.load(open(pj))
            p["id"] = nid()
            p["main_timeline_id"] = d["id"]
            for tline in p["timelines"]:
                if tline["id"] == old_tid:
                    tline["id"] = d["id"]
            json.dump(p, open(pj, "w"), ensure_ascii=False, separators=(",", ":"))
        for bak in (os.path.join(tdir, "project.json.bak"), info_path + ".bak"):
            if os.path.exists(bak):
                os.remove(bak)
    json.dump(d, open(info_path, "w"), ensure_ascii=False, separators=(",", ":"))
    if os.path.isdir(os.path.join(tdir, d["id"])):
        shutil.copy(info_path, os.path.join(tdir, d["id"], "draft_info.json"))
    mp = os.path.join(dst, "draft_meta_info.json")
    meta = json.load(open(mp))
    meta.update(tm_duration=d["duration"], tm_draft_modified=now)
    if new:
        meta.update(draft_id=nid(), draft_name=name, draft_fold_path=dst, tm_draft_create=now,
                    cloud_package_completed_time="", tm_draft_cloud_completed="",
                    tm_draft_cloud_entry_id=-1, tm_draft_cloud_modified=0,
                    tm_draft_cloud_parent_entry_id=-1, tm_draft_cloud_space_id=-1,
                    tm_draft_cloud_user_id=-1)
    json.dump(meta, open(mp, "w"), ensure_ascii=False, separators=(",", ":"))


def detect_roles(d):
    """트랙 역할: 이름이 붙어 있으면 그대로, 아니면 알려진 구성(템플릿 / 이전 구성)으로 추정."""
    names = [t.get("name", "") for t in d["tracks"]]
    known = set(ROLES) | {f"효과음{k}" for k in range(3, 10)}  # 이름 붙은 draft
    if all(names) and set(names) <= known:
        return names
    # CapCut에서 사용자가 손댄 트랙은 이름이 지워질 수 있음 → 빈 이름은 종류로 채움(오디오 = 비어 있는 효과음 번호)
    known |= {n for n in names if n.startswith("사용자추가")}
    if set(n for n in names if n) <= known and sum(1 for n in names if not n) <= 4:
        filled = list(names)
        for i, (t, n) in enumerate(zip(d["tracks"], names)):
            if n:
                continue
            if t["type"] == "audio":
                k = next(k for k in range(1, 10) if f"효과음{k}" not in filled)
                filled[i] = f"효과음{k}"
            elif t["type"] == "text" and len(t["segments"]) > 3 and "나레이션자막" not in filled:
                filled[i] = "나레이션자막"
            else:  # 사용자가 CapCut에서 직접 추가한 트랙 — 손대지 않고 보존
                filled[i] = f"사용자추가{sum(1 for x in filled if x.startswith('사용자추가')) + 1}"
        return filled
    if len(names) == 10 and names[-1] == "나레이션":
        return V2_ROLES
    if len(names) == len(TEMPLATE_ROLES):
        return TEMPLATE_ROLES
    sys.exit(f"트랙 구성을 알 수 없음: {[(t['type'], n) for t, n in zip(d['tracks'], names)]}")


EPISODE_ROOTS = kit_config.paths("episode_roots")


def add_media(dst, ep):
    """CapCut 미디어 목록(draft_meta_info.json draft_materials)에 편집본·원본 추가, 다른 회차 파일은 뺌."""
    mp = os.path.join(dst, "draft_meta_info.json")
    meta = json.load(open(mp))
    nfc = lambda p: unicodedata.normalize("NFC", p)
    here = nfc(os.path.abspath(ep)) + os.sep

    def foreign(p):  # 채널 폴더 안이지만 이 회차 폴더 밖 = 템플릿에서 따라온 남의 회차 파일
        p = nfc(p or "")
        return any(p.startswith(nfc(r) + os.sep) for r in EPISODE_ROOTS) and not p.startswith(here)
    removed = 0
    for g in meta["draft_materials"]:
        keep = [v for v in g["value"] if not foreign(v.get("file_Path"))]
        removed += len(g["value"]) - len(keep)
        g["value"] = keep
    group = next((g for g in meta["draft_materials"] if g["type"] == 0), None)
    if group is None:
        group = {"type": 0, "value": []}
        meta["draft_materials"].append(group)
    have = {nfc(v["file_Path"]) for v in group["value"]}
    added = [f"다른 회차 {removed}개 뺌"] if removed else []
    ep = os.path.abspath(ep)
    srcs = sorted(glob.glob(os.path.join(glob.escape(ep), "source", "*.mp4")))
    cutf = sorted(glob.glob(os.path.join(glob.escape(ep), "cuts", "[0-9]*.mov")))
    for g in meta["draft_materials"]:  # 지난 버전의 컷 파일 중 없어진 것은 목록에서 뺌
        g["value"] = [v for v in g["value"] if os.path.exists(v.get("file_Path") or "") or
                      os.sep + "cuts" + os.sep not in (v.get("file_Path") or "")]
    have = {nfc(v["file_Path"]) for v in group["value"]}
    for path in reversed([os.path.join(ep, "edit.mp4"), os.path.join(ep, "original.mp4")] + srcs + cutf):
        f = os.path.relpath(path, ep)
        if not os.path.exists(path) or nfc(path) in have:
            continue
        w, h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", path], capture_output=True,
                              text=True).stdout.strip().split(",")
        group["value"].insert(0, {"create_time": int(time.time()), "duration": us(probe_dur(path)),
                                  "extra_info": f, "file_Path": path, "height": int(h),
                                  "id": str(uuid.uuid4()), "import_time": int(time.time()),
                                  "import_time_ms": int(time.time() * 1000), "item_source": 1,
                                  "md5": "", "metetype": "video", "roughcut_time_range":
                                      {"duration": -1, "start": -1}, "sub_time_range":
                                      {"duration": -1, "start": -1}, "type": 0, "width": int(w)})
        added.append(f)
    json.dump(meta, open(mp, "w"), ensure_ascii=False, separators=(",", ":"))
    return added


def snapshot(ep, dst, name, note=""):
    """현재 draft 상태를 <회차>/work/capcut_versions/<draft>/vNN/ 에 저장. → NN"""
    root = os.path.join(ep, "work", "capcut_versions", name)
    os.makedirs(root, exist_ok=True)
    n = 1 + max([int(x[1:3]) for x in os.listdir(root) if re.match(r"v\d\d", x)], default=0)
    vd = os.path.join(root, f"v{n:02d}_{time.strftime('%m%d_%H%M')}")
    os.makedirs(vd)
    for f in ("draft_info.json", "draft_meta_info.json"):
        shutil.copy(os.path.join(dst, f), vd)
    if note:
        open(os.path.join(vd, "메모.txt"), "w").write(note + "\n")
    return n


def restore(ep, dst, name, n):
    root = os.path.join(ep, "work", "capcut_versions", name)
    vd = os.path.join(root, next(x for x in sorted(os.listdir(root)) if x.startswith(f"v{int(n):02d}")))
    d = json.load(open(os.path.join(vd, "draft_info.json")))
    cur = json.load(open(os.path.join(dst, "draft_info.json")))
    d["id"] = cur["id"]
    save(dst, d, name, False)
    print(f"되돌림: {vd}")


def base_name(name):
    return re.sub(r"_v\d+$", "", name)


def resolve_draft(name):
    """이름이 없으면 같은 이름의 가장 높은 버전(<이름>_vNN) draft를 찾음."""
    if os.path.exists(os.path.join(ROOT, name)):
        return name
    cands = [x for x in os.listdir(ROOT) if re.fullmatch(re.escape(base_name(name)) + r"_v\d+", x)]
    if not cands:
        sys.exit(f"없는 draft: {name}")
    return max(cands, key=lambda x: int(x.rsplit("_v", 1)[1]))


def rename_draft(old, new):
    """draft 폴더·이름 바꾸기(CapCut 꺼진 상태에서): 폴더, draft_meta_info, draft_info, root_meta_info."""
    if old == new:
        return new
    src, dst = os.path.join(ROOT, old), os.path.join(ROOT, new)
    if os.path.exists(dst):
        sys.exit(f"이미 있는 이름: {new}")
    os.rename(src, dst)
    mp = os.path.join(dst, "draft_meta_info.json")
    meta = json.load(open(mp))
    meta.update(draft_name=new, draft_fold_path=dst)
    json.dump(meta, open(mp, "w"), ensure_ascii=False, separators=(",", ":"))
    for ip in [os.path.join(dst, "draft_info.json")] + glob.glob(
            os.path.join(glob.escape(dst), "Timelines", "*", "draft_info.json")):
        d = json.load(open(ip))
        d["name"] = new
        json.dump(d, open(ip, "w"), ensure_ascii=False, separators=(",", ":"))
    rp = os.path.join(ROOT, "root_meta_info.json")
    if os.path.exists(rp):
        r = json.load(open(rp))
        for e in r.get("all_draft_store", []):
            if e.get("draft_fold_path") == src:
                for k, v in list(e.items()):
                    if isinstance(v, str) and v.startswith(src):
                        e[k] = dst + v[len(src):]
                e["draft_name"] = new
        json.dump(r, open(rp, "w"), ensure_ascii=False, separators=(",", ":"))
    return new


def quit_capcut():
    subprocess.run(["osascript", "-e", 'tell application "CapCut" to quit'], capture_output=True)
    for _ in range(60):  # 본체 + 보조 프로세스(Helper)까지 끝나야 다시 열 수 있음
        if subprocess.run(["pgrep", "-f", "/Applications/CapCut.app/"],
                          capture_output=True).returncode != 0:
            return True
        time.sleep(0.5)
    return False


def open_capcut():
    for _ in range(3):
        subprocess.run(["open", "-a", "CapCut"])
        for _ in range(20):
            if capcut_running():
                return True
            time.sleep(0.5)
    print("⚠ CapCut을 다시 열지 못했습니다. 직접 열어 주세요.")
    return False


def main(ep, name, *opts):
    relaunch = "--relaunch" in opts and capcut_running()
    if relaunch and not quit_capcut():
        sys.exit("CapCut이 종료되지 않았습니다.")
    if capcut_running() and "SHORTS_CAPCUT_ROOT" not in os.environ:
        sys.exit("CapCut이 실행 중입니다. 종료한 뒤 다시 실행하세요.")
    update = "--update" in opts
    allp = {"title", "subs", "video", "narr", "sfx", "mix", "order", "media", "extras"}  # cuts는 지정할 때만
    vals = {opts[i + 1] for i, o in enumerate(opts[:-1]) if o in ("--from", "--restore")}
    free = [o for o in opts if not o.startswith("--") and o not in vals]
    parts = set(free[0].split(",")) & (allp | {"cuts"}) if free else set()
    parts = parts or allp
    if "--update" in opts or "--restore" in opts:
        name = resolve_draft(name)
    dst = os.path.join(ROOT, name)
    kit_config.require("capcut_template_dir")
    tpl = Draft(json.load(open(os.path.join(TEMPLATE_DIR, "draft_info.json"))))
    if "--restore" in opts:
        return restore(ep, dst, name, opts[opts.index("--restore") + 1])
    if update:
        if not os.path.exists(dst):
            sys.exit(f"없는 draft: {dst}")
        cur = json.load(open(os.path.join(dst, "draft_info.json")))
        vroot = os.path.join(ep, "work", "capcut_versions", name)
        if not os.path.isdir(vroot) or not os.listdir(vroot):
            snapshot(ep, dst, name, "수정 전 처음 상태")
        if "--from" in opts:
            src = os.path.join(ROOT, opts[opts.index("--from") + 1], "draft_info.json")
            info = json.load(open(src))
            info["id"], info["name"] = cur["id"], cur.get("name", name)  # 이 draft의 타임라인 id 유지
        else:
            info = cur
    else:
        if os.path.exists(dst):
            sys.exit(f"이미 있는 draft: {dst} (고치려면 --update)")
        shutil.copytree(TEMPLATE_DIR, dst)
        parts = allp
        info = json.load(open(os.path.join(dst, "draft_info.json")))
    log = []
    dr = Draft(info)
    bd = Builder(ep, dr, tpl, detect_roles(dr.d))
    if not update or "cuts" in parts:
        bd.video_split()
        log.append(f"컷 파일 {len(bd.cut_bounds)}개 · 후보컷 {bd.alt_cuts()}개(숨김)")
    if not update:
        bd.sticker_emph()
    if "title" in parts:
        bd.title(); log.append("제목")
    if "subs" in parts:
        log.append(f"자막 {bd.subtitles()}개")
    cuts = bd.video_flash() if parts & {"video", "sfx"} else []
    if "narr" in parts:
        log.append(f"나레이션 {bd.narration()}조각")
    if "sfx" in parts:
        log.append(f"효과음 {bd.sfx(cuts)}개(전환 {len(cuts)})")
    if "mix" in parts:
        bd.mix(); log.append("음량")
    if "extras" in parts:
        log.append(f"후보 영상 {bd.extras()}개(타임라인 끝)")
    if "order" in parts:
        bd.order(); log.append("트랙 순서·켜기")
    save(dst, dr.d, name, not update)
    if "media" in parts:
        log.append("미디어 " + ("·".join(add_media(dst, ep)) or "그대로"))
    import versions  # 회차 파일 + draft를 한 버전으로(되돌리기는 versions.py restore)
    n = versions.save(ep, name, ", ".join(log))
    name = rename_draft(name, f"{base_name(name)}_v{n:02d}")  # 프로젝트 하나, 이름에 지금 버전
    print(f"{'수정' if update else '생성'}: {name} ({bd.D:.2f}초) — " + ", ".join(log))
    if relaunch:
        open_capcut()


if __name__ == "__main__":
    main(*sys.argv[1:])
