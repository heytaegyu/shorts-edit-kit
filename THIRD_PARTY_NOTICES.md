# 외부 코드·모델·자산

이 저장소에는 아래 파일을 **넣지 않았다**. 각자 받아 설치하며 각 라이선스를 따른다. 공개 전 확인일: 2026-11-06. 조건은 바뀔 수 있으니 사용 전에 원문을 확인한다.

| 항목 | 라이선스 | 이 저장소의 처리 | 출처 |
|---|---|---|---|
| Bandit v2 코드 | Apache-2.0 | 미포함. 커밋 `d5563d9`를 직접 clone | https://github.com/kwatcharasupat/bandit-v2 |
| Bandit v2 가중치 `checkpoint-eng.ckpt` | CC-BY-SA-4.0 | 미포함(약 447MB). 직접 다운로드, md5 `9b74787e7f752709ce986ba1b1ac29a9` | https://zenodo.org/records/12701995 |
| Whisper 모델 `mlx-community/whisper-large-v3-turbo` | 상위 OpenAI 모델은 MIT. mlx 변환 저장소에는 라이선스 표기가 없어 사용 전 확인 필요 | 미포함. 첫 실행 때 내려받음 | https://huggingface.co/mlx-community/whisper-large-v3-turbo |
| FFmpeg | 빌드에 따라 LGPL/GPL | 미포함. 직접 설치 | https://ffmpeg.org/legal.html |
| 글꼴 | 글꼴마다 다름 | 미포함. 설정 `font_path`로 지정 | 각 글꼴 배포처 |
| CapCut 템플릿·효과·애니메이션 JSON | 재배포 권한 불명확 | 미포함. 자기 CapCut draft에서 직접 준비 | https://www.capcut.com/material-license-agreement |
| 효과음·BGM | 라이브러리마다 다름 | 미포함. 설정 `sfx_dirs`로 지정 | 각 라이브러리 |
| ElevenLabs | 유료 API, 서비스 약관 | 코드는 API만 호출. 키와 목소리는 사용자 몫 | https://elevenlabs.io |

## Python 패키지

| 패키지 | 라이선스 | 파일 |
|---|---|---|
| numpy | BSD-3-Clause 등 | `requirements.txt`, `requirements-bandit.txt` |
| Pillow | MIT-CMU | `requirements.txt` |
| pyloudnorm | MIT | `requirements.txt` |
| mlx-whisper | MIT | `requirements-whisper.txt` |
| torch, torchaudio, soundfile | BSD 계열 | `requirements-bandit.txt` |
| pytorch-lightning, torchmetrics | Apache-2.0 | `requirements-bandit.txt` |
| librosa | ISC | `requirements-bandit.txt` |
| PyYAML | MIT | `requirements-bandit.txt` |

## 알림
- 이 프로젝트는 CapCut, ByteDance, ElevenLabs와 관련이 없다. CapCut draft 형식은 공식 공개 규격이 아니어서 CapCut 버전에 따라 동작하지 않을 수 있다. CapCut 약관 적합성은 각자 확인한다.
- 원본 영상과 음성의 저작권은 사용자 책임이다. 사용 권리가 있는 자료만 쓴다.
