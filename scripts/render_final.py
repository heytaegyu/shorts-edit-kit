"""CapCut draft → 검토용 mp4 (CapCut 없이 ffmpeg로).

사용: python scripts/render_final.py <회차> <draft 이름(버전 생략 가능)>
출력: <회차>/export/<draft>_검토용.mp4 (1080x1920, 30fps)
- 영상: 「영상」 트랙(보이는 클립) — 컷 파일을 타임라인 위치에. 숨긴 클립(후보컷 등)은 뺌.
- 글자: 보이는 텍스트 트랙(제목·자막·나레이션자막 등)을 설정 글꼴로 그림(색 범위·크기·테두리·그림자 근사).
  애니메이션(Fade·Zoom), 스티커, Flash Out 전환은 빠짐 — 최종본은 CapCut 내보내기가 기준.
- 소리: 영상 클립 소리 + 오디오 트랙 전부(음량·위치 그대로).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import capcut_export as cc  # noqa: E402
import kit_config  # noqa: E402

W, H, FPS = 1080, 1920, 30
FONT = kit_config.font()
# CapCut 글자 크기 → 화면 px: 제목 25.1이면 「가나다 라마 바사 아」(8자+띄어쓰기 3개)가 화면 폭의 90%(960px)에 꽉 참(실측 규칙)
_f = ImageFont.truetype(FONT, 100)
PX_PER_SIZE = 960 / (_f.getlength("가나다 라마 바사 아") / 100) / 25.13


def text_png(mat, path):
    c = json.loads(mat["content"])
    text, styles = c["text"], c["styles"]
    color, size, stroke = [None] * len(text), [14.0] * len(text), [0.0] * len(text)
    for st in styles:
        a, b = st["range"]
        col = st["fill"]["content"]["solid"]["color"]
        for i in range(a, min(b, len(text))):
            color[i] = tuple(int(round(x * 255)) for x in col)
            size[i] = st.get("size", 14)
            stroke[i] = (st.get("strokes") or [{"width": 0}])[0]["width"]
    shadow = any(st.get("shadows") for st in styles)
    lines, k = [], 0
    for ln in text.split("\n"):
        lines.append([(ch, color[k + j] or (255, 255, 255), size[k + j], stroke[k + j]) for j, ch in enumerate(ln)])
        k += len(ln) + 1
    ls = float(mat.get("line_spacing") or 0)
    rows = []
    for ln in lines:
        px = max([s for _, _, s, _ in ln] or [14]) * PX_PER_SIZE
        width = sum(ImageFont.truetype(FONT, round(s * PX_PER_SIZE)).getlength(ch) for ch, _, s, _ in ln)
        rows.append((ln, px, width))
    pad = 40
    tw = int(max(w for _, _, w in rows) + 2 * pad) if rows else 10
    th = int(sum(px * (1.12 + ls) for _, px, _ in rows) + 2 * pad)
    img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    sh = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sh)
    y = pad
    for ln, px, width in rows:
        x = (tw - width) / 2
        for ch, col, s, sw in ln:
            f = ImageFont.truetype(FONT, round(s * PX_PER_SIZE))
            spx = max(0, round(sw * s * PX_PER_SIZE * 1.1))
            base = y + (px - s * PX_PER_SIZE)
            if shadow:
                ds.text((x + 6, base + 6), ch, font=f, fill=(0, 0, 0, 180))
            d.text((x, base), ch, font=f, fill=col + (255,), stroke_width=spx, stroke_fill=(0, 0, 0, 255))
            x += f.getlength(ch)
        y += px * (1.12 + ls)
    if shadow:
        sh = sh.filter(ImageFilter.GaussianBlur(3))
        sh.alpha_composite(img)
        img = sh
    img.save(path)
    return tw, th


def main(ep, name):
    ep = os.path.abspath(ep)
    name = cc.resolve_draft(name)
    dst = os.path.join(cc.ROOT, name)
    d = json.load(open(os.path.join(dst, "draft_info.json")))
    mats = {m["id"]: m for v in d["materials"].values() if isinstance(v, list) for m in v
            if isinstance(m, dict) and "id" in m}
    path_of = lambda m: re.sub(r"##_draftpath_placeholder_[^#]+_##", dst, m["path"])
    dur = d["duration"] / 1e6
    tmp = tempfile.mkdtemp(prefix="shorts_final_")
    inputs, vf, af = [], [], []

    def add_input(p, *pre):
        inputs.extend([*pre, "-i", p])
        return len([x for x in inputs if x == "-i"]) - 1

    vf.append(f"color=black:s={W}x{H}:r={FPS}:d={dur:.3f}[v0]")
    cur, n = "v0", 0
    tracks = [t for t in d["tracks"] if not (t.get("attribute", 0) & 2)]
    video = next(t for t in tracks if t.get("name") == "영상")
    for s in video["segments"]:
        if s.get("visible") is False:
            continue
        m = mats[s["material_id"]]
        tr, sr = s["target_timerange"], s["source_timerange"]
        a, du, ss = tr["start"] / 1e6, tr["duration"] / 1e6, sr["start"] / 1e6
        i = add_input(path_of(m))
        sc = s["clip"]["scale"]["x"]
        side = round(1080 * sc)
        vf.append(f"[{i}:v]trim=start={ss:.6f}:duration={du:.6f},setpts=PTS-STARTPTS+{a:.6f}/TB,"
                  f"scale={side}:{side}[c{i}]")
        y = round(H / 2 - side / 2 - s["clip"]["transform"]["y"] * H / 2)
        x = round(W / 2 - side / 2 + s["clip"]["transform"]["x"] * W / 2)
        vf.append(f"[{cur}][c{i}]overlay={x}:{y}:eof_action=pass:enable='between(t,{a:.4f},{a + du - 0.0005:.4f})'[v{i}]")
        cur = f"v{i}"
        if s.get("volume", 1) > 0:
            af.append(f"[{i}:a]atrim=start={ss:.6f}:duration={du:.6f},asetpts=PTS-STARTPTS,"
                      f"volume={s['volume']:.4f},adelay={round(a * 1000)}:all=1[a{i}]")
    for t in tracks:
        if t["type"] != "text":
            continue
        for s in t["segments"]:
            if s.get("visible") is False:
                continue
            m = mats[s["material_id"]]
            n += 1
            png = os.path.join(tmp, f"t{n}.png")
            tw, th = text_png(m, png)
            tr = s["target_timerange"]
            a, b = tr["start"] / 1e6, (tr["start"] + tr["duration"]) / 1e6
            cx = W / 2 + s["clip"]["transform"]["x"] * W / 2
            cy = H / 2 - s["clip"]["transform"]["y"] * H / 2
            i = add_input(png)
            vf.append(f"[{cur}][{i}:v]overlay={round(cx - tw / 2)}:{round(cy - th / 2)}:"
                      f"enable='between(t,{a:.4f},{b:.4f})'[v{i}]")
            cur = f"v{i}"
    for t in tracks:
        if t["type"] != "audio":
            continue
        for s in t["segments"]:
            if s.get("visible") is False or s.get("volume", 1) <= 0:
                continue
            m = mats[s["material_id"]]
            p = path_of(m)
            tr, sr = s["target_timerange"], s["source_timerange"]
            a, du, ss = tr["start"] / 1e6, tr["duration"] / 1e6, sr["start"] / 1e6
            if subprocess.run(["ffprobe", "-v", "error", p], capture_output=True).returncode:
                # CapCut 컴파운드(라이저+셔터)는 암호화돼 읽을 수 없음 → 셔터 소리만 같은 시각에
                if os.path.exists(cc.FLASH_SFX) and "combination" in p:
                    shot = a + cc.SHUTTER_AT - ss
                    p, a, ss, du = cc.FLASH_SFX, max(0.0, shot), 0.0, min(1.0, dur - max(0.0, shot))
                else:
                    print("⚠ 읽을 수 없는 소리 파일(뺌):", p)
                    continue
            sp = s.get("speed", 1.0) or 1.0
            i = add_input(p)
            af.append(f"[{i}:a]atrim=start={ss:.6f}:duration={du * sp:.6f},asetpts=PTS-STARTPTS,"
                      + (f"atempo={sp:.4f}," if abs(sp - 1) > 1e-3 else "") +
                      f"volume={s['volume']:.4f},adelay={round(a * 1000)}:all=1[a{i}]")
    labels = "".join("[" + x.rsplit("[", 1)[1] for x in af)
    af.append(f"{labels}amix=inputs={len(af)}:normalize=0:dropout_transition=0,"
              f"atrim=duration={dur:.3f},alimiter=limit=0.97[aout]")
    graph = os.path.join(tmp, "graph.txt")
    open(graph, "w").write(";\n".join(vf + af))
    out_dir = os.path.join(ep, "export")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{name}_검토용.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-/filter_complex", graph,
                    "-map", f"[{cur}]", "-map", "[aout]", "-t", f"{dur:.3f}", "-r", str(FPS),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    print(f"검토용 mp4: {out} ({dur:.2f}초, 글자 {n}개, 소리 {len(af) - 1}개)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
