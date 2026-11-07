# shorts-edit-kit

영어 원본 영상으로 한국어 나레이션·자막 쇼츠를 만드는 명령줄 도구와 제작 규칙 모음입니다. 스토리보드 JSON을 넣으면 1:1 편집본, 자막, 자동 검수 보고서, CapCut 프로젝트(draft)가 나옵니다.

*English: command-line tools and editing rules that turn an English source video and a JSON storyboard into a Korean-subtitled short: rendered edit, subtitles, QA report, and a CapCut draft. Documentation is in Korean.*

> 사용 권리가 있는 영상과 음성만 쓰세요. CapCut, ElevenLabs와 관련 없는 비공식 프로젝트입니다.

## 기능

| 단계 | 스크립트 | 입력 → 출력 |
|---|---|---|
| 받아쓰기 | `transcribe.py` | 영상 → 단어별 시각 JSON (Whisper, Apple Silicon) |
| 대사 분리 | `bandit_separate.py` | 영상 → `dx.wav`, `mx.wav`, `fx.wav` (Bandit v2) |
| 스토리보드 변환 | `from_storyboard.py` | `data.json`, `plan.json` → `card.json`, `cuts.json`, `subs_plan.json` |
| 나레이션 | `narrate.py` | `card.json` → mp3와 글자 시각 (ElevenLabs, 유료, 바뀐 줄만 녹음) |
| 렌더 | `render_edit.py` | → `edit.mp4`, `narration.wav`, `preview.mp4`, `timeline.json` |
| 자막 | `make_subs.py` | → `subs.json`, `edit.ko.srt`, `edit.en.srt` |
| 싱크 맞춤 | `sync_subs.py` | 원본 대사 자막을 실제 단어 시각에 맞춤 |
| 검수 | `qa.py` | → `qa_report.md` (컷 조각, 무음, 음량, 자막 길이) |
| CapCut | `capcut_export.py` | → CapCut draft, 컷 파일, `컷번호.md` |
| 버전 | `versions.py` | 회차 파일과 draft를 묶어 저장·복원 |
| 대본 반영 | `apply_script.py` | `대본.txt` ↔ 회차 파일 |
| 검토용 영상 | `render_final.py` | draft → 검토용 mp4 (애니메이션·스티커·플래시 없음) |

제작 규칙은 `rules/`에 있습니다. 규칙끼리 또는 규칙과 코드가 다른 부분은 [`OPEN_ITEMS.md`](OPEN_ITEMS.md)에 미정으로 적었습니다.

## 설치

macOS(Apple Silicon)의 Python 3.12에서 확인했습니다. FFmpeg가 필요합니다.

```bash
brew install ffmpeg python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.local.json   # 값을 자기 환경에 맞게 고침
```

필요한 기능만 추가로 설치합니다.

```bash
# 받아쓰기, qa.py의 소리 검사
.venv/bin/pip install -r requirements-whisper.txt

# 대사 분리 (별도 가상환경)
python3.12 -m venv .venv-bandit
.venv-bandit/bin/pip install -r requirements-bandit.txt
git clone https://github.com/kwatcharasupat/bandit-v2 vendor/bandit-v2
git -C vendor/bandit-v2 checkout d5563d9
# https://zenodo.org/records/12701995 에서 checkpoint-eng.ckpt를 받아 vendor/bandit-ckpt/ 에 둠
```

## 직접 준비할 것

저장소에 넣지 않은 파일입니다. 이유는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를 보세요.

| 설정 | 내용 | 필요한 단계 |
|---|---|---|
| `font_path` | 한글 글꼴 파일 | 자막, CapCut, 검토용 영상 |
| `capcut_template_dir` | 자기 CapCut 템플릿 draft 폴더 | CapCut |
| `capcut_assets_dir` | 자기 draft에서 꺼낸 `flash_out_anim.json`, `zoom_in_anim.json` | CapCut |
| `capcut_file_sfx_name` | 템플릿 효과음 중 나레이션 견본으로 쓸 소재 이름 | CapCut |
| `sfx_dirs`, `flash_sfx` | 효과음 파일 | 효과음 |
| `elevenlabs_voice_id`, 환경변수 `ELEVENLABS_API_KEY` 또는 `elevenlabs_key_file` | 나레이션 목소리와 키 | 나레이션 |

템플릿은 트랙 8개가 이 순서여야 합니다: 영상, 프레임, 제목, 스티커, 자막(프리셋 2개), 효과음1, BGM, 효과음2. 효과음 트랙에는 이름이 `Compound`로 시작하는 전환음이 있어야 플래시 전환을 쓸 수 있습니다. `tests/run_demo.py`의 `template()`이 필요한 구조의 최소 예시입니다.

`config.local.json`은 커밋되지 않습니다. 다른 위치의 설정은 환경변수 `SHORTS_CONFIG`로 지정합니다.

## 실행

회차 폴더에 `source/<원본>.mp4`, `card.json`, `cuts.json`, `subs_plan.json`, `work/storyboard/data.json`, `work/storyboard/plan.json`, `work/src_words.json`이 있어야 합니다. 형식은 `tests/run_demo.py`의 `storyboard()`를 참고하세요.

```bash
PY=.venv/bin/python; EP=episodes/my-episode

$PY scripts/transcribe.py $EP/source/video.mp4 $EP/work/src_words.json "고유명사 힌트"
BANDIT_HOP=2.0 .venv-bandit/bin/python scripts/bandit_separate.py $EP/source/video.mp4 $EP/audio "10-20,35-48"
$PY scripts/from_storyboard.py $EP
$PY scripts/narrate.py $EP
$PY scripts/render_edit.py $EP
$PY scripts/make_subs.py $EP
$PY scripts/sync_subs.py $EP
$PY scripts/qa.py $EP
$PY scripts/capcut_export.py $EP my_draft        # CapCut을 닫고 실행
$PY scripts/capcut_export.py $EP my_draft --update subs,title
$PY scripts/render_final.py $EP my_draft
```

## 가상 예시로 점검

실제 영상, 음성, 대본, 유료 API 없이 임시 폴더에서 돌립니다. 실제 CapCut 프로젝트 폴더는 건드리지 않습니다.

```bash
.venv/bin/python tests/run_demo.py --font /System/Library/Fonts/AppleSDGothicNeo.ttc
```

색 화면과 사인파로 만든 예시라서 `qa.py`가 소리 경고를 내는 것은 정상입니다. 통과 여부는 마지막 줄의 `N/N 통과`로 봅니다.

## 확인 범위

| 상태 | 항목 |
|---|---|
| 가상 예시로 실행 | 스토리보드 변환, 나레이션 재사용, 렌더, 자막, 싱크, 검수, 대본 내보내기, 가상 템플릿에 draft 쓰기·수정, 버전 목록, 검토용 mp4 |
| 합성 음성으로 로컬 실행 | Whisper 받아쓰기, Bandit 대사 분리 |
| 새 가상환경에서 설치·실행 | `requirements.txt`, `requirements-whisper.txt`, `requirements-bandit.txt`, Bandit 코드 clone (macOS Apple Silicon, Python 3.12) |
| 실행 안 함 | ElevenLabs 실제 호출 |
| 확인 불가 | 실제 CapCut에서 draft 열기, `--relaunch`, macOS 외 환경 |

CapCut draft 형식은 공식 규격이 아니므로 CapCut 버전에 따라 동작하지 않을 수 있습니다.

## 한계

- 원본 언어는 영어, 자막은 한국어 기준입니다(`language="en"`, 달러→원화 처리).
- 경로 기본값은 macOS CapCut 기준이고, CapCut 실행 확인과 재시작에 macOS 명령을 씁니다.
- 받아쓰기는 `mlx-whisper`라 Apple Silicon 전용입니다.

## 라이선스

이 저장소의 코드와 문서는 [MIT](LICENSE)입니다. 외부 구성 요소는 각자의 라이선스를 따릅니다. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를 보세요.
