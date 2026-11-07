"""영어 받아쓰기(단어 타임스탬프) → words JSON.

사용: python scripts/transcribe.py <media> <out.json> [initial_prompt]
"""
import json
import sys

import mlx_whisper

import kit_config

MODEL = kit_config.get("whisper_model")
BASE_PROMPT = kit_config.get("whisper_base_prompt")


def main(src, out, extra=""):
    r = mlx_whisper.transcribe(src, path_or_hf_repo=MODEL, language="en", word_timestamps=True,
                               condition_on_previous_text=False,
                               initial_prompt=f"{BASE_PROMPT} {extra}".strip())
    segs = [{"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"].strip(),
             "words": [{"w": w["word"].strip(), "s": round(w["start"], 3), "e": round(w["end"], 3),
                        "p": round(w.get("probability", 0), 3)} for w in s.get("words", [])]}
            for s in r["segments"]]
    json.dump({"src": src, "segments": segs}, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
