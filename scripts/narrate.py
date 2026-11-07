"""일레븐랩스 나레이션: card.json의 narr 줄마다 mp3 + 글자 타임스탬프 생성.

사용: python scripts/narrate.py <회차 폴더>
출력: <회차>/narration/NN.mp3, narration.json (줄별 길이·글자 타이밍)
문구가 그대로인 줄은 기존 mp3를 다시 씀(바뀐 줄만 생성 — 크레딧 절약, 목소리 유지).
키는 환경변수 ELEVENLABS_API_KEY 또는 설정 elevenlabs_key_file에서만 읽고 어디에도 출력하지 않는다.
새로 녹음할 줄이 있을 때만 키와 voice_id를 요구한다.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

import kit_config

VOICE_ID = kit_config.get("elevenlabs_voice_id")
SETTINGS = {"stability": 0.75, "similarity_boost": 0.75, "style": 0,
            "use_speaker_boost": True, "speed": 1.05}


def tts(text, key):
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/with-timestamps"
        "?output_format=mp3_44100_128",  # 192는 Creator 이상 요금제
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                         "voice_settings": SETTINGS}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json",
                 "User-Agent": "shorts-edit-kit/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", path], capture_output=True, text=True).stdout)


def main(ep):
    ep = os.path.abspath(ep)  # 절대경로로 저장: CapCut은 상대경로를 못 찾음 → Media lost
    card = json.load(open(os.path.join(ep, "card.json")))
    out_dir = os.path.join(ep, "narration")
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(ep, "narration.json")
    # 같은 문구의 기존 녹음은 줄 번호가 바뀌어도 다시 씀(먼저 메모리에 읽어 둬서 덮어쓰기 안전)
    old = {}
    if os.path.exists(jp):
        for l in json.load(open(jp))["lines"]:
            if os.path.exists(l["file"]):
                old[l["text"]] = (l, open(l["file"], "rb").read())
    lines = []
    for i, s in enumerate(x for x in card["script"] if x["type"] == "narr"):
        if s["text"] in old:
            prev, data = old[s["text"]]
            path = os.path.join(out_dir, f"{i:02d}.mp3")
            open(path, "wb").write(data)
            lines.append({**prev, "i": i, "file": path})
            print(f"{i:02d} (그대로) {s['text']}")
            continue
        kit_config.require("elevenlabs_voice_id")
        r = tts(s["text"], kit_config.elevenlabs_key())
        path = os.path.join(out_dir, f"{i:02d}.mp3")
        open(path, "wb").write(base64.b64decode(r["audio_base64"]))
        al = r.get("alignment") or r["normalized_alignment"]  # normalized는 로마자로 옴
        lines.append({"i": i, "text": s["text"], "file": path, "dur": round(duration(path), 3),
                      "chars": al["characters"],
                      "starts": al["character_start_times_seconds"],
                      "ends": al["character_end_times_seconds"]})
        print(f"{i:02d} {lines[-1]['dur']:.2f}s {s['text']}")
    json.dump({"voice_id": VOICE_ID, "settings": SETTINGS, "lines": lines},
              open(os.path.join(ep, "narration.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
