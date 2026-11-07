"""설정 읽기. 경로·계정 값은 코드에 두지 않고 여기서만 가져온다.

읽는 순서: 환경변수 SHORTS_CONFIG가 가리키는 JSON → 저장소 맨 위 config.local.json → 아래 기본값.
config.local.json은 커밋하지 않는다(.gitignore). 항목 설명은 config.example.json과 README 참고.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "font_path": "",                 # 자막·제목 폭 계산과 검토용 mp4에 쓰는 글꼴 파일
    "capcut_root": "~/Movies/CapCut/User Data/Projects/com.lveditor.draft",  # macOS 기본 위치
    "capcut_template_dir": "",       # 직접 만든 CapCut 템플릿 draft 폴더
    "capcut_assets_dir": os.path.join(REPO, "assets"),  # flash_out_anim.json, zoom_in_anim.json을 둘 폴더
    "capcut_file_sfx_name": "",      # 템플릿 효과음 풀에서 파일 효과음·나레이션 견본으로 쓸 소재 이름
    "sfx_dirs": [],                  # 효과음 파일 폴더 목록
    "flash_sfx": "",                 # 전환 셔터 소리 파일
    "transition_sfx_prefixes": [],   # 가장 큰 지점을 컷에 맞출 효과음 이름 앞부분
    "episode_roots": [],             # 회차 폴더들의 상위 폴더(템플릿에서 따라온 다른 회차 파일을 뺄 때 사용)
    "elevenlabs_voice_id": "",       # 나레이션 목소리
    "elevenlabs_key_file": "~/.config/elevenlabs/api_key",  # 환경변수 ELEVENLABS_API_KEY가 먼저
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "whisper_base_prompt": "",       # 받아쓰기 힌트(고유명사 등)
    "bandit_repo": os.path.join(REPO, "vendor", "bandit-v2"),
    "bandit_ckpt": os.path.join(REPO, "vendor", "bandit-ckpt", "checkpoint-eng.ckpt"),
}


def _load():
    p = os.environ.get("SHORTS_CONFIG") or os.path.join(REPO, "config.local.json")
    if os.path.exists(p):
        with open(p, encoding="utf8") as f:
            return json.load(f)
    if os.environ.get("SHORTS_CONFIG"):
        sys.exit(f"설정 파일이 없습니다: {p}")
    return {}


_cfg = {**DEFAULTS, **_load()}


def get(key):
    return _cfg[key]


def _abs(v):
    """~와 상대경로(저장소 맨 위 기준)를 절대경로로."""
    return os.path.join(REPO, os.path.expanduser(v))


def path(key):
    v = _cfg[key]
    return _abs(v) if v else ""


def paths(key):
    return [_abs(v) for v in _cfg[key]]


def require(key):
    v = _cfg[key]
    if not v:
        sys.exit(f"설정이 필요합니다: {key} (config.example.json 참고)")
    return v


def font():
    p = _abs(require("font_path"))
    if not os.path.exists(p):
        sys.exit(f"글꼴 파일이 없습니다: {p} (설정 font_path)")
    return p


def elevenlabs_key():
    k = os.environ.get("ELEVENLABS_API_KEY")
    if k:
        return k.strip()
    p = path("elevenlabs_key_file")
    if not p or not os.path.exists(p):
        sys.exit("ElevenLabs 키가 없습니다: 환경변수 ELEVENLABS_API_KEY 또는 설정 elevenlabs_key_file")
    with open(p) as f:
        return f.read().strip()
