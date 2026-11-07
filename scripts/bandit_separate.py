"""Bandit v2 (DnR v3) 시네마틱 음원 분리: 대사(dx) / 음악(mx) / 효과음(fx).

사용: .venv-bandit/bin/python scripts/bandit_separate.py <media> <out_dir> [ranges]
- ranges: "a-b,c-d" (초). 주면 그 구간(앞뒤 3초 여유)만 분리하고 나머지는 무음으로 채워
  원본과 길이가 같은 wav를 만든다(시간축 유지).
- 원본 inference.py는 CUDA 고정 + ray 의존이라, 모델과 청크 추론 핸들러만 직접 조립해 MPS/CPU로 돌림.
- 출력: out_dir/dx.wav, mx.wav, fx.wav (48kHz, 원본 채널 수)
"""
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # istft 일부 연산만 CPU로

import subprocess
import sys

import numpy as np
import soundfile as sf
import torch

import kit_config

sys.path.insert(0, kit_config.path("bandit_repo"))

from src.models.bandit.bandit import Bandit  # noqa: E402
from src.system.inference_handler import StandardTensorChunkedInferenceHandler  # noqa: E402

FS = 48000
STEMS = ["speech", "music", "sfx"]
OUT_NAMES = {"speech": "dx", "music": "mx", "sfx": "fx"}
MODEL_KWARGS = dict(  # configs/models/bandit-mus64.yaml
    in_channels=1, band_type="musical", n_bands=64, normalize_channel_independently=False,
    treat_channel_as_feature=True, n_sqm_modules=8, emb_dim=128, rnn_dim=256,
    bidirectional=True, rnn_type="GRU", mlp_dim=512, hidden_activation="Tanh",
    hidden_activation_kwargs=None, complex_mask=True, use_freq_weights=True, n_fft=2048,
    win_length=2048, hop_length=512, window_fn="hann_window", wkwargs=None, power=None,
    center=True, normalized=True, pad_mode="reflect", onesided=True)


def load_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", str(FS),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()  # (2, n)


CKPT = kit_config.path("bandit_ckpt")
PAD = 3.0


def main(src, out_dir, ranges=""):
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model = Bandit(stems=STEMS, fs=FS, **MODEL_KWARGS)
    state = torch.load(CKPT, map_location="cpu", weights_only=False)["state_dict"]
    state = {k[len("model."):]: v for k, v in state.items() if k.startswith("model.")}
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        raise RuntimeError(f"체크포인트에 없는 가중치: {missing[:5]}")
    model.float().to(dev).eval()  # MPS는 float64 미지원
    handler = StandardTensorChunkedInferenceHandler(
        chunk_size_seconds=8.0, hop_size_seconds=float(os.environ.get("BANDIT_HOP", "2.0")), inference_batch_size=4, fs=FS).float().to(dev)

    full = load_audio(src)  # (2, n)
    n = full.shape[1]
    spans = ([(0, n)] if not ranges else
             [(max(0, int((float(a) - PAD) * FS)), min(n, int((float(b) + PAD) * FS)))
              for a, b in (r.split("-") for r in ranges.split(","))])
    outs = {stem: np.zeros_like(full) for stem in STEMS}
    for s0, s1 in spans:
        audio = torch.from_numpy(full[:, s0:s1].copy())[None].to(dev)
        with torch.inference_mode():
            out = handler(audio, model)
        for stem in STEMS:
            outs[stem][:, s0:s1] = out["estimates"][stem]["audio"][0].float().cpu().numpy()
    os.makedirs(out_dir, exist_ok=True)
    for stem in STEMS:
        sf.write(os.path.join(out_dir, f"{OUT_NAMES[stem]}.wav"), outs[stem].T, FS,
                 subtype="PCM_24")
    print("unexpected keys:", len(unexpected))


if __name__ == "__main__":
    main(*sys.argv[1:])
