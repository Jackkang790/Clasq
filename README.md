# Clasq — AI 파일 관리 시스템

로컬 AI 모델을 활용해 파일을 분석하고, 검색하고, 폴더를 추천·정리하는 데스크탑 애플리케이션입니다.

---

## 주요 기능

| 화면 | 기능 |
|---|---|
| **검색** | SQLite 기반 로컬 텍스트 검색, 동의어·별칭 처리, 증분 인덱싱 |
| **정리** | AI가 분석한 파일을 기존 폴더 구조 기반으로 추천 → 수락 / 건너뛰기 / 직접 지정 |
| **저장목록** | 검색·분석 결과 북마크 관리 |
| **설정** | AI 백엔드 연결, 폴더 경로, 분석 옵션 관리 |

### AI 분석 지원 형식
- **이미지**: OCR(EasyOCR + Tesseract), 색상 분석, Qwen VL 설명 생성
- **동영상**: 장면 분할(ffmpeg), 프레임 캡처, Whisper 음성 인식
- **문서**: PDF / DOCX / PPTX / XLSX / HWP 텍스트 추출

### 폴더 추천 엔진 (`src/recommendation/`)
- 기존 폴더들의 파일 패턴으로 **FolderProfile** 생성
- TF-IDF 기반 후보 검색 → Qwen 리랭킹(선택)으로 최적 폴더 결정
- ACCEPTED / OVERRIDDEN / SKIPPED / STALE 상태 관리, 전체 수락 지원

---

## 기술 스택

- **UI**: PySide6 (프레임리스 윈도우, QSS 스타일)
- **AI 백엔드**: Qwen3-VL-8B (로컬 vLLM 서버, OpenAI 호환 API)
- **OCR**: EasyOCR, Tesseract
- **음성**: OpenAI Whisper
- **DB**: SQLite (파일 레지스트리, 검색 스냅샷)
- **검색**: 로컬 텍스트 인덱스 + 동의어 맵 + 쿼리 파서

---

## 설치

```bash
pip install -r requirements.txt
```

> ffmpeg가 필요한 경우 `FFMPEG_PATH` 환경변수로 경로를 지정하세요.

---

## 실행

```bash
python main.py
```

---

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AI_BASE_URL` | `http://127.0.0.1:8100/v1` | vLLM 서버 주소 |
| `AI_MODEL` | `qwen3-vl-8b` | 사용할 모델명 |
| `AI_TIMEOUT` | `300` | 요청 타임아웃 (초) |
| `AI_CONCURRENCY` | `2` | 동시 분석 스레드 수 (1–4) |
| `AI_MAX_TOKENS` | `1000` | 응답 최대 토큰 |
| `VIDEO_AI_TIMEOUT` | `900` | 동영상 분석 타임아웃 (초) |
| `FFMPEG_PATH` | _(시스템 PATH)_ | ffmpeg 실행 파일 경로 |

---

## 브랜치 구조

| 브랜치 | 내용 |
|---|---|
| `main` | 안정 릴리스 |
| `AI` | AI 분석·추천 기능 개발 |
| `FrontEnd` | UI/UX 개발 |
