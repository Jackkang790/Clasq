# Clasq — AI 파일 관리 시스템

로컬 GPU에서 실행되는 AI 모델을 활용해 파일을 분석하고, 검색하고, 폴더를 추천·정리하는 Windows 데스크탑 애플리케이션입니다.
사용자가 Python, Docker, llama.cpp, ffmpeg를 직접 설치하지 않아도 앱만 설치하면 동작합니다.

---

## 주요 기능

| 화면 | 기능 |
|---|---|
| **검색** | SQLite 기반 로컬 텍스트 검색, 동의어·별칭 처리, 증분 인덱싱 |
| **정리** | AI가 분석한 파일을 기존 폴더 구조 기반으로 추천 → 수락 / 건너뛰기 / 직접 지정 |
| **저장목록** | 검색·분석 결과 북마크 관리 |
| **설정** | 폴더 경로, 분석 옵션 관리 |

### AI 분석 지원 형식

| 형식 | 분석 내용 |
|---|---|
| **이미지** (JPG, PNG, WEBP 등) | OCR 텍스트 추출, 색상 분석, Qwen VL 설명 생성, JSON 메타데이터 구조화 |
| **동영상** (MP4, MKV, AVI) | ffmpeg 장면 분할, 대표 프레임 추출(최대 24장), Qwen VL 멀티이미지 분석 |
| **문서** (PDF, DOCX, PPTX, XLSX, HWP/HWPX) | 텍스트 추출 후 AI 태그·분류 생성 |
| **오디오** (MP3, WAV, M4A) | Whisper 음성 인식(선택) |

### 폴더 추천 엔진 (`src/recommendation/`)

- 기존 폴더의 파일 패턴으로 **FolderProfile** 자동 생성
- TF-IDF 기반 후보 검색 → Qwen 리랭킹(선택)으로 최적 폴더 결정
- 상태 관리: `ACCEPTED` / `OVERRIDDEN` / `SKIPPED` / `STALE`, 전체 수락 지원

---

## 시스템 요구사항

| 항목 | 요구사항 |
|---|---|
| OS | Windows 10/11 64bit |
| GPU | NVIDIA GPU (VRAM 14GB 이상 권장) |
| NVIDIA 드라이버 | 최신 버전 권장 |
| 디스크 여유공간 | 앱 설치 약 2GB + AI 모델 약 5.8GB = **약 8GB** |

> NVIDIA 드라이버 외에 CUDA Toolkit, Python, Docker, WSL은 설치하지 않아도 됩니다.

---

## 설치

### 배포판 설치 (권장)

[Releases](https://github.com/Jackkang790/Clasq/releases) 에서 `Clasq_Setup_x.x.x.exe` 를 다운로드하여 실행합니다.

```
설치 경로: C:\Program Files\Clasq\
모델 경로: %LOCALAPPDATA%\Clasq\models\  (최초 실행 시 자동 다운로드)
```

### 최초 실행 흐름

```
Clasq 실행
→ GPU 자동 감지 (HardwareDetector)
→ 실행 프로필 선택 (RTX 3090급 이상 → Q4_K_M 프로필)
→ AI 모델 없으면 자동 다운로드 (Qwen3-VL-8B Q4_K_M, 약 5.8GB)
→ SHA-256 무결성 검증
→ llama-server 자동 시작
→ AI 기능 활성화
```

두 번째 실행부터는 모델이 이미 있으므로 다운로드 없이 즉시 시작됩니다.

---

## AI 추론 구조

```
Clasq.exe
  │  (HTTP, 127.0.0.1:8080)
  ▼
llama-server.exe (번들 포함)
  │  (Qwen3-VL-8B Q4_K_M GGUF)
  ▼
NVIDIA GPU (로컬)
```

- 모든 AI 추론은 로컬에서 처리됩니다. 파일·이미지가 외부 서버로 전송되지 않습니다.
- OpenAI 호환 API(`/v1/chat/completions`)를 사용합니다.

---

## 개발 환경 실행

```bash
pip install -r requirements.txt
python main.py
```

### 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AI_BASE_URL` | `http://127.0.0.1:8080/v1` | llama-server 주소 |
| `AI_MODEL` | `qwen3-vl-8b` | 모델명 |
| `AI_TIMEOUT` | `300` | 요청 타임아웃 (초) |
| `AI_CONCURRENCY` | `2` | 동시 분석 스레드 수 (1–4) |
| `AI_MAX_TOKENS` | `1000` | 응답 최대 토큰 |
| `VIDEO_AI_TIMEOUT` | `900` | 동영상 분석 타임아웃 (초) |
| `LLAMA_SERVER_EXE` | `runtime/llama-server.exe` | llama-server 실행 파일 경로 |
| `LLAMA_MODEL_PATH` | `%LOCALAPPDATA%\Clasq\models\qwen3vl-8b-q4_k_m.gguf` | 모델 파일 경로 |
| `LLAMA_MMPROJ_PATH` | `%LOCALAPPDATA%\Clasq\models\mmproj-bf16.gguf` | Vision projector 경로 |
| `LLAMA_MANAGED` | `true` | `false`이면 외부 서버 수동 실행 모드 |
| `FFMPEG_PATH` | _(번들 또는 PATH)_ | ffmpeg 실행 파일 경로 |

---

## 기술 스택

| 분류 | 도구 / 버전 |
|---|---|
| UI | PySide6 6.11.1 (프레임리스 윈도우, QSS 스타일) |
| AI 추론 | llama.cpp build b10549, Qwen3-VL-8B Q4\_K\_M GGUF |
| Vision projector | mmproj-bf16.gguf |
| 영상 처리 | ffmpeg 8.1.2 (번들) |
| DB | SQLite (파일 레지스트리, 검색 스냅샷) |
| 검색 | 로컬 텍스트 인덱스 + TF-IDF + 동의어 맵 |
| 패키징 | PyInstaller 6.22.2 (one-dir) |
| 설치 프로그램 | Inno Setup 6.7.3 |
| 개발 언어 | Python 3.13 |

---

## 빌드

```powershell
# 1. PyInstaller 빌드
.\scripts\build_windows.ps1

# 2. 설치 프로그램 생성
.\scripts\build_installer.ps1
```

산출물: `dist/installer/Clasq_Setup_1.0.0.exe` (~685MB)
