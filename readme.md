# Codex AI 작업 지침

## 작업 목적

기존 PySide6 기반 파일 정리 프로그램의 **기존 UI와 기능을 최대한 유지하면서** 아래 기능을 수정 및 추가한다.

특히 이번 작업에서 **파일 태깅 기능은 반드시 `setting_view`에 적용되는 기능**임을 명확히 한다.

`organize_view`의 파일 태깅 기능과 혼동하지 않는다.

---

# 1. AI 파일 태깅 기능 개선

## 적용 위치

> **중요: 이 기능은 `setting_view`에 적용한다.**

파일 태깅 기능의 UI 및 실행 로직은 반드시 기존 `setting_view`의 파일 태깅 기능을 기준으로 수정한다.

```text
setting_view
    ↓
파일 경로 지정
    ↓
파일 태깅 실행
    ↓
파일 내용 분석
    ↓
AI 태그 생성
    ↓
태그 저장
```

**`organize_view`에는 파일 태깅 Progress Dialog를 추가하지 않는다.**

---

## 태그 생성 우선순위

파일에 태그를 부착할 때는 **파일의 실제 내용(Content)을 가장 우선적으로 참조​**한다.

```text
파일 선택
   ↓
파일 내용 분석
   ↓
내용 분석 성공?
   ├─ YES → AI를 이용하여 내용 기반 태그 생성
   │
   └─ NO
       ↓
     파일 확장자 확인
       ↓
     확장자 기반 기본 태그 생성
```

즉, 확장자를 먼저 확인하여 태그를 지정하면 안 된다.

**파일 내용 분석에 실패했을 경우에만 확장자 기반 fallback 태깅을 수행한다.**

---

## 확장자 기반 Fallback 태그

| 파일 종류 | 확장자 예시 | 기본 태그 |
|---|---|---|
| 이미지 | `.jpg`, `.jpeg`, `.png`, `.bmp`, `.webp` 등 | `이미지` |
| GIF | 정적 GIF | `이미지` |
| GIF | 움직이는 GIF | `움짤` |
| 텍스트 | `.txt`, `.md`, `.csv`, `.log` 등 | `텍스트` |
| 문서 | `.xlsx`, `.xls`, `.hwp`, `.hwpx`, `.doc`, `.docx`, `.pdf` 등 | `문서` |
| 프레젠테이션 | `.ppt`, `.pptx` | `ppt` |

예:

```text
photo.jpg          → 이미지
photo.png          → 이미지
animation.gif      → 움짤
memo.txt           → 텍스트
data.csv           → 텍스트
report.xlsx        → 문서
document.hwp       → 문서
presentation.pptx  → ppt
```

### GIF 처리

GIF는 단순히 `.gif` 확장자만 확인하지 않는다.

- 프레임이 1개인 정적 GIF → `이미지`
- 여러 프레임으로 구성된 움직이는 GIF → `움짤`

---

# 2. `setting_view`의 파일 태깅 Progress Dialog

## 적용 위치

> **매우 중요: 파일 태깅 Progress Dialog는 `setting_view`의 파일 태깅 작업에만 적용한다.**

기존 `setting_view`에서 사용자가 파일 경로를 지정하고 파일에 AI 태그를 부착하는 작업을 수행할 때 Progress Dialog를 표시한다.

`organize_view`의 경로 추가 기능과 혼동하지 않는다.

---

## UI 디자인

파일 태깅 Progress Dialog는 기존 프로젝트의 **`organize_view` → "경로 추가하기"에서 사용하고 있는 Dialog의 디자인을 참조**한다.

기존 Dialog의 디자인과 일관되도록 구현한다.

---

## 파일 태깅 Dialog 예시

```text
┌─────────────────────────────────────┐
│             파일 태깅 중             │
├─────────────────────────────────────┤
│                                     │
│       파일을 분석하고 있습니다...      │
│                                     │
│       [██████████░░░░░░░] 60%        │
│                                     │
│       6 / 10 파일 처리 중             │
│                                     │
└─────────────────────────────────────┘
```

### 표시 정보

최소한 다음 정보를 표시한다.

```text
제목:
파일 태깅 중

상태:
파일을 분석하고 있습니다...

Progress Bar:
[██████████░░░░░░░] 60%

진행 상황:
6 / 10 파일 처리 중
```

가능하다면 현재 처리 중인 파일 이름도 표시한다.

```text
현재 파일:
report.xlsx
```

---

## 진행률

파일이 10개라면 실제 처리 개수에 따라 다음과 같이 갱신한다.

```text
1 / 10 → 10%
2 / 10 → 20%
3 / 10 → 30%
...
6 / 10 → 60%
...
10 / 10 → 100%
```

Progress Bar의 값은 실제 파일 처리 진행 상황과 연동한다.

---

# 3. Ollama 모델 로딩 Progress Dialog

## 프로그램 기동 시 표시

프로그램 실행 시 Ollama 모델을 로딩하는 과정도 사용자에게 보여준다.

단순한 텍스트 메시지가 아니라 **파일 태깅 Progress Dialog와 동일한 계열의 UI**를 사용한다.

예:

```text
┌─────────────────────────────────────┐
│          Ollama 모델 로딩 중          │
├─────────────────────────────────────┤
│                                     │
│       AI 모델을 불러오고 있습니다...    │
│                                     │
│       [██████████░░░░░░░] 60%        │
│                                     │
│       Gemma 모델 로딩 중...           │
│                                     │
└─────────────────────────────────────┘
```

### Progress Dialog 구성

- Dialog 제목
- 현재 작업 상태
- Progress Bar
- 진행률 또는 현재 단계
- 모델 로딩 상태

실제 Ollama에서 정확한 Progress 정보를 확인할 수 있다면 실제 값을 사용한다.

정확한 Progress 값을 얻을 수 없는 경우에는 단계별 Progress 또는 Indeterminate Progress Bar를 사용한다.

임의의 가짜 진행률을 빠르게 증가시키는 방식은 사용하지 않는다.

---

# 4. Ollama 모델 로딩 Thread 처리

Ollama 모델 로딩 때문에 프로그램 UI가 멈추면 안 된다.

권장 구조:

```text
Main GUI Thread
        │
        ├─ Ollama Progress Dialog 표시
        │
        └─ Worker Thread
                │
                └─ Ollama 모델 로딩
                        │
                        ├─ Progress Signal
                        ├─ Status Signal
                        └─ Finished Signal
                                  │
                                  ↓
                         Main GUI Thread
                                  │
                                  ↓
                         Dialog 종료
```

Worker Thread에서 직접 UI를 변경하지 않는다.

Signal/Slot을 이용하여 GUI Thread에서 Progress Dialog를 업데이트한다.

기존 프로젝트에 `QThread`, Worker 또는 Signal/Slot 구조가 있다면 이를 우선 활용한다.

---

# 5. `organize_view`에 "프리셋 불러오기" 버튼 추가

## 적용 위치

이번 기능은 **`organize_view`에만 적용**한다.

기존 `organize_view`의 버튼 배치를 유지하면서 기존 **`경로 추가하기` 버튼의 바로 왼쪽**에 `프리셋 불러오기` 버튼을 추가한다.

### 변경 전

```text
[경로 추가하기] [자동정리하기]
```

### 변경 후

```text
[프리셋 불러오기] [경로 추가하기] [자동정리하기]
```

> **중요: 기존 `자동정리하기` 버튼은 그대로 유지한다.**
>
> **`프리셋 불러오기` 버튼은 반드시 `경로 추가하기`와 `자동정리하기` 사이가 아니라, `경로 추가하기`의 바로 왼쪽에 추가한다.**

즉, 최종 버튼 순서는 반드시 다음과 같아야 한다.

```text
[프리셋 불러오기] [경로 추가하기] [자동정리하기]
```

---

## 버튼 디자인

`프리셋 불러오기` 버튼은 기존 `경로 추가하기` 버튼과 동일한 디자인을 사용한다.

- 동일한 크기
- 동일한 폰트
- 동일한 색상
- 동일한 Hover 효과
- 동일한 Border
- 동일한 여백
- 동일한 스타일

기존 `경로 추가하기` 버튼의 구현 방식을 참고하여 추가한다.

기존 UI 디자인을 새로 만들지 않는다.

---

# 6. 프리셋 데이터

프리셋은 `file_manager.db`의 다음 테이블을 사용한다.

```text
managed_paths
```

사용 컬럼:

```text
id
path
```

매핑:

```text
id   → 프리셋 이름
path → 불러올 실제 경로
```

예:

```text
id | path
-------------------------------
1  | C:/Users/test/Documents
2  | D:/Project
3  | D:/Images
```

프리셋 목록에서는 `id`를 이름으로 사용한다.

사용자가 `2`를 선택하면:

```text
D:/Project
```

를 `organize_view`의 경로 목록으로 불러온다.

---

# 7. 프리셋 불러오기 동작

```text
[프리셋 불러오기]
        ↓
managed_paths 조회
        ↓
저장된 id 목록 표시
        ↓
사용자가 프리셋 선택
        ↓
선택한 id의 path 조회
        ↓
organize_view 경로 목록에 추가
```

### 주의사항

- 기존 `file_manager.db` 스키마를 변경하지 않는다.
- 새로운 프리셋 테이블을 만들지 않는다.
- 기존 DB 접근 방식을 우선 사용한다.
- 이미 등록된 경로의 중복 처리 방식은 기존 로직을 따른다.
- 기존 `경로 추가하기` 기능은 그대로 유지한다.
- 기존 `자동정리하기` 기능도 그대로 유지한다.

---

# 8. 저장목록 테이블 더블클릭 수정 시 글자 깨짐 해결

## 문제

저장목록 테이블에서 컬럼 레코드를 더블클릭하여 수정할 때 한글이나 특정 문자가 깨지는 문제가 있다.

다음 전체 흐름을 확인한다.

```text
더블클릭
   ↓
셀 편집
   ↓
문자열 수정
   ↓
DB UPDATE
   ↓
DB SELECT
   ↓
테이블 갱신
```

---

## 확인해야 할 부분

다음 부분을 확인하고 문제를 해결한다.

1. `QTableWidget` / `QTableView`
2. Delegate
3. `itemChanged`
4. `cellChanged`
5. `item.text()`
6. DB UPDATE
7. DB SELECT
8. `str` / `bytes` 변환
9. UTF-8 처리
10. Windows 시스템 인코딩 처리

Python에서 이미 `str`인 값을 불필요하게 `.encode()` / `.decode()` 하지 않는다.

SQLite에서도 불필요한 인코딩 변환을 추가하지 않는다.

수정 후 DB에 저장된 한글이 다시 조회되어도 정상적으로 표시되어야 한다.

---

# 9. 기능별 적용 위치 정리

| 기능 | 적용 위치 |
|---|---|
| 파일 내용 기반 AI 태깅 | **`setting_view`** |
| 파일 확장자 기반 fallback 태깅 | **`setting_view`** |
| 파일 태깅 Progress Dialog | **`setting_view`** |
| Ollama 모델 로딩 Progress Dialog | 프로그램 기동 시 |
| 프리셋 불러오기 버튼 | **`organize_view`** |
| `managed_paths` 프리셋 조회 | **`organize_view`** |
| 저장목록 더블클릭 문자 깨짐 수정 | 저장목록 테이블 |

### 중요

**파일 태깅 기능과 파일 태깅 Progress Dialog는 `setting_view`에 적용한다.**

**프리셋 불러오기 기능은 `organize_view`에 적용한다.**

두 기능을 서로 다른 View에 구현해야 한다.

---

# 10. Thread 및 UI 공통 원칙

PySide6 GUI 객체는 GUI Thread에서만 조작한다.

```text
Worker Thread
│
├─ Ollama 모델 로딩
├─ 파일 내용 분석
├─ AI 태깅
└─ DB 처리
        │
        │ Signal
        ↓
Main GUI Thread
│
├─ Progress Dialog
├─ Progress Bar
├─ Label
├─ Table
└─ 기타 UI
```

특히 `setting_view`에서 파일 태깅을 수행하는 동안 UI가 멈추지 않아야 한다.

---

# 11. 기존 코드 우선 분석

코드를 수정하기 전에 반드시 현재 프로젝트 구조를 확인한다.

특히 다음을 찾아본다.

```text
setting_view
├─ 파일 경로 지정 기능
├─ 파일 태깅 기능
└─ 기존 AI 태깅 처리

organize_view
├─ 경로 추가하기
├─ 기존 Dialog
├─ 자동정리하기
└─ 경로 목록

Ollama
├─ Ollama Manager
├─ 모델 초기화
└─ 모델 로딩

Database
├─ file_manager.db
├─ managed_paths
└─ 저장목록

Thread
├─ QThread
├─ Worker
└─ Signal / Slot
```

이미 비슷한 기능이 존재한다면 새로 만들지 말고 기존 코드를 확장한다.

---

# 12. 기존 UI 보존 원칙

이번 작업의 목적은 기존 프로그램을 유지하면서 필요한 기능만 추가 및 수정하는 것이다.

따라서 다음을 임의로 변경하지 않는다.

- 기존 `setting_view` 레이아웃
- 기존 `organize_view` 레이아웃
- 기존 버튼 디자인
- 기존 Dialog 디자인
- 기존 Table 디자인
- 기존 AI 태깅 로직
- 기존 DB 구조
- 기존 경로 추가 기능
- 기존 자동정리하기 기능

추가되는 UI는 기존 UI의 디자인과 일관성을 유지한다.

---

# 13. 최종 테스트 체크리스트

## `setting_view` 파일 태깅

- [ ] 파일 태깅 기능이 **`setting_view`에 적용되어 있는가?**
- [ ] `organize_view`에 파일 태깅 기능을 잘못 추가하지 않았는가?
- [ ] 파일 내용 분석이 확장자 분석보다 우선하는가?
- [ ] 내용 분석 실패 시 확장자 기반 fallback이 실행되는가?
- [ ] JPG/PNG 등의 파일이 `이미지`로 태깅되는가?
- [ ] 정적 GIF가 `이미지`로 태깅되는가?
- [ ] 움직이는 GIF가 `움짤`로 태깅되는가?
- [ ] TXT/CSV 등의 파일이 `텍스트`로 태깅되는가?
- [ ] XLSX/HWP 등의 파일이 `문서`로 태깅되는가?
- [ ] PPTX가 `ppt`로 태깅되는가?

## `setting_view` 파일 태깅 Progress Dialog

- [ ] 파일 태깅 시작 시 Progress Dialog가 표시되는가?
- [ ] Dialog 제목이 `파일 태깅 중`인가?
- [ ] `파일을 분석하고 있습니다...` 등의 상태가 표시되는가?
- [ ] Progress Bar가 표시되는가?
- [ ] 현재 파일 수 / 전체 파일 수가 표시되는가?
- [ ] 실제 태깅 진행률과 Progress Bar가 연동되는가?
- [ ] 태깅 완료 후 Dialog가 자동으로 닫히는가?
- [ ] 태깅 중 UI가 멈추지 않는가?
- [ ] 기존 `organize_view`의 `경로 추가하기` Dialog와 디자인이 통일되어 있는가?

## Ollama 모델 로딩

- [ ] 프로그램 기동 시 Ollama 모델 로딩 Dialog가 표시되는가?
- [ ] `Ollama 모델 로딩 중` 제목이 표시되는가?
- [ ] `AI 모델을 불러오고 있습니다...` 등의 상태가 표시되는가?
- [ ] Progress Bar가 표시되는가?
- [ ] 실제 로딩 상태 또는 단계가 Progress에 반영되는가?
- [ ] 모델 로딩 완료 후 Dialog가 자동으로 닫히는가?
- [ ] 모델 로딩 중 UI가 멈추지 않는가?
- [ ] 모델 로딩 실패 시 Dialog가 정상적으로 종료되는가?

## `organize_view` 프리셋

- [ ] 변경 전 버튼 순서가 `[경로 추가하기] [자동정리하기]`였음을 기준으로 수정했는가?
- [ ] 변경 후 버튼 순서가 **`[프리셋 불러오기] [경로 추가하기] [자동정리하기]`**인가?
- [ ] `프리셋 불러오기`가 `경로 추가하기` 바로 왼쪽에 있는가?
- [ ] `자동정리하기` 버튼이 기존 위치에 그대로 유지되는가?
- [ ] `프리셋 불러오기`와 `경로 추가하기`의 디자인이 동일한가?
- [ ] `managed_paths`를 정상적으로 조회하는가?
- [ ] `id`를 프리셋 이름으로 사용하는가?
- [ ] `path`를 실제 경로로 불러오는가?
- [ ] 선택한 경로가 `organize_view`에 정상적으로 추가되는가?

## 저장목록

- [ ] 테이블 셀을 더블클릭하여 수정할 수 있는가?
- [ ] 수정한 한글이 깨지지 않는가?
- [ ] DB 저장 후에도 한글이 정상적으로 유지되는가?
- [ ] DB에서 다시 조회해도 문자가 정상적으로 표시되는가?

## 안정성

- [ ] 기존 기능이 정상적으로 동작하는가?
- [ ] PySide6 Thread 관련 오류가 발생하지 않는가?
- [ ] Ollama 로딩 중 UI가 멈추지 않는가?
- [ ] `setting_view` 파일 태깅 중 UI가 멈추지 않는가?
- [ ] Progress Dialog가 작업 완료 또는 오류 발생 후 반드시 종료되는가?
- [ ] 프로그램 종료 시 Worker Thread가 정상적으로 종료되는가?
---

# 클래스별 기능 정리

저장소에 존재하는 모든 클래스의 소속 파일, 책임, 핵심 메서드를 정리한다.

## 진입점 / 인프라

### `MainWindow` (`main.py`)
- 프레임리스 메인 윈도우. 상단바 · 사이드바 · `QStackedWidget`(설정/검색/정리/저장목록)을 구성한다.
- `ClasqCore`와 `RefreshManager`를 생성해 모든 뷰에 주입한다.
- 핵심: `_navigate()`, `_go_back()`, `_go_forward()`, `_refresh_data_models()`, `_animated_minimize()`, `_toggle_pseudo_maximize()`

### `OllamaManager` (`ollama_manager.py`)
- 로컬 Ollama 설치 · 서버 실행 · 모델 다운로드 · REST 호출을 담당하는 정적 클래스.
- 핵심: `is_installed()`, `install()`, `is_running()`, `start_server()`, `model_exists()`, `download_model()`, `request()`, `test_model()`, `generate()`, `initialize()`

### `RefreshManager` (`src/ui/refresh_manager.py`)
- DB가 변경됐을 때 `database_changed` 시그널로 모든 화면을 한 번에 동기화한다.
- 핵심: `refresh()`

## 코어 파이프라인

### `ClasqCore` (`src/utils/core.py`)
- DB · 추출기 · 분석기 · 검색엔진을 묶는 애플리케이션 코어.
- 파일 처리 순서는 **내용 분석 우선**이며, 추출 또는 이미지 전처리가 실패한 경우에만 확장자 폴백 태그를 사용한다.
- 핵심: `process_file_upload()`, `process_user_query()`, `process_folder_batch()`, `sync_db_with_disk()`, `scan_directory_files()`, `get_saved_files()`, `update_saved_file()`, `build_organize_preview()`, `group_files_by_tags()`, `organize_files()`, `get_all_files()`

### `MainProcessor` (`src/utils/core.py`)
- 기존 호출부 호환을 위한 `ClasqCore` 별칭 서브클래스.

### `FilePreprocessError` (`src/utils/file_pipeline.py`)
- 파일 읽기/해석 단계의 오류를 나타내는 사용자 정의 예외.

### `ExtensionTagger` (`src/utils/file_pipeline.py`)
- **내용 분석이 실패한 경우에만** 사용하는 확장자 기반 폴백 태거.
- 이미지(`.jpg/.jpeg/.png/.bmp/.webp/.tiff`) → `이미지`, 정적 GIF → `이미지`, 움직이는 GIF → `움짤`, 텍스트(`.txt/.md/.csv/.log` 등) → `텍스트`, 문서(`.pdf/.doc/.docx/.xls/.xlsx/.hwp/.hwpx`) → `문서`, `.ppt/.pptx` → `ppt`, 그 외 → `미분류`
- 핵심: `tag_for()`, `is_animated_gif()`

### `TextExtractor` (`src/utils/file_pipeline.py`)
- 확장자별 원문/이미지 데이터 추출. 이미지 리사이즈·JPEG 변환, PDF/DOCX/XLSX/PPTX/HWP/HWPX 파싱, UTF-8 실패 시 CP949 폴백, 압축·대용량·손상 파일 차단.
- 핵심: `is_image_file()`, `process_image()`, `extract()`, `_sanitize_text()`

### `FileAnalyzer` (`src/utils/file_pipeline.py`)
- Ollama 텍스트/비전 모델을 호출해 표시 이름·태그·설명을 생성하고 응답 JSON을 정규화한다.
- 분석 실패 시 `ExtensionTagger` 태그를 담은 폴백 응답을 만든다.
- 핵심: `analyze_document_text()`, `analyze_image_bytes()`, `_get_file_info()`, `_normalize_tags()`, `_build_fallback_response()`

### `FileRegistryManager` (`src/utils/db_manager.py`)
- SQLite(`file_manager.db`) 접근 계층. WAL·busy timeout·UTF-8 커넥션 설정, 스키마 마이그레이션, 해시 기반 중복 감지와 `_duplicates` 격리, 파일 레코드 CRUD, 관리 경로(`managed_paths`) 관리.
- 문자열은 변환 없이 Python `str` 그대로 파라미터 바인딩하므로 한글이 깨지지 않는다.
- 핵심: `save_file_result()`, `sync_with_disk()`, `add_managed_path()`, `get_managed_paths()`, `get_managed_path_presets()`, `remove_managed_path()`, `update_tags()`, `rename_file()`, `delete_file()`, `get_all_files()`, `bulk_session()`

### `SearchQueryParser` (`src/utils/query_parser.py`)
- 자연어 입력을 검색/대화 의도로 분류하고 검색어·확장자·날짜 범위를 추출한다. Ollama 실패 시 규칙 기반 폴백.
- 핵심: `parse_user_query()`, `_normalize_extensions()`, `_extract_date_range()`, `_fallback_search()`

### `SearchEngine` (`src/utils/search_engine.py`)
- 파싱 결과를 DB 질의로 변환한다. 불용어 제거, 동의어 확장, AND 검색 후 OR 폴백, 확장자·날짜 필터링.
- 핵심: `process_query_result()`, `search_files_smart()`, `_execute_sql_query()`

## Worker 스레드

### `FolderScanAndTagWorker` (`src/utils/workers.py`)
- `setting_view`에서 선택한 경로를 스캔하고 파일별 AI 태깅을 GUI 스레드 밖에서 수행한다.
- 시그널: `progress(str)`, `fileProgress(int, int, str)`(순번/전체/파일명), `taggingFinished()`, `finished(dict)`, `error(str)`
- UI는 직접 건드리지 않고 시그널만 방출한다.

### `OllamaInitWorker` (`src/utils/workers.py`)
- 프로그램 기동 시 Ollama 설치 확인 → 서버 시작 → 모델 확인/다운로드 → 모델 응답 테스트의 4단계를 백그라운드로 수행한다.
- 시그널: `progress(int, int, str)`(완료 단계/전체 단계/상태 문구), `completed(bool, str)`

### `QueryParseWorker` (`src/utils/workers.py`), `QueryProcessWorker` (`src/ui/views/search_view.py`)
- 자연어 검색 파싱/처리를 백그라운드에서 실행하고 결과를 시그널로 전달한다.

## 공용 위젯

### `TaskProgressDialog` (`src/ui/widgets/progress_dialog.py`)
- 파일 태깅과 Ollama 모델 로딩이 공유하는 Progress Dialog. 제목 · 상태 문구 · Progress Bar · `n / total 처리 중` · 현재 항목명을 표시한다.
- 전체 개수를 알 수 없으면 Indeterminate 모드로 동작하며 가짜 진행률을 만들지 않는다.
- 핵심: `update_progress(current, total, detail, status)`

### `Sidebar` (`src/ui/components/side_bar.py`)
- 좌측 내비게이션. `page_changed(int)` 시그널로 스택 인덱스를 전환한다.

### `TitleBar` (`src/ui/components/title_bar.py`) / `_AnimatedIconButton`
- 커스텀 상단바(뒤로/앞으로/설정/최소화/최대화/닫기)와 호버 애니메이션 아이콘 버튼.

### `FileUploadView` (`src/ui/widgets/fileupload_view.py`)
- 드래그 앤 드롭 파일 업로드 영역 위젯.

## 화면(View)

### `SettingsView` (`src/ui/views/settings_view.py`)
- 관리 경로 추가/삭제/저장, 프리셋(JSON) 관리, **파일 태깅 실행 화면**.
- `start_tagging()`이 `FolderScanAndTagWorker`와 `TaskProgressDialog`("파일 태깅 중")를 함께 띄우고, `fileProgress`로 실제 처리 개수를 반영한 뒤 완료·오류 시 Dialog를 자동으로 닫는다.
- 핵심: `start_tagging()`, `on_tagging_file_progress()`, `on_tagging_finished()`, `on_tagging_error()`, `load_paths_from_db()`, `add_root()`, `del_root()`, `save_preset()`, `load_preset()`

### `CheckBoxHeader` (`src/ui/views/settings_view.py`)
- 경로 테이블의 전체 선택 체크박스를 그리는 커스텀 헤더.

### `OrganizeView` (`src/ui/views/organize_view.py`)
- 파일 자동 정리 화면. 태그 기반 그룹 미리보기와 실제 파일 이동을 담당한다.
- `프리셋 불러오기`는 `managed_paths`의 `id`를 프리셋 이름으로, `path`를 실제 경로로 사용하며 선택한 경로를 기존 경로 추가 로직으로 넘겨 중복 처리를 그대로 따른다.
- 핵심: `_on_load_preset()`, `_on_path_added()`, `_load_files_from_db()`, `_on_auto_organize()`, `_on_organize_confirmed()`

### `_FileTableScreen` (`src/ui/views/organize_view.py`)
- 정리 대상 파일 테이블 화면. 버튼 순서는 `[프리셋 불러오기] [경로 추가하기] [자동 정리하기]`이며 프리셋 버튼은 경로 추가 버튼과 동일한 보조 버튼 스타일을 쓴다.
- 시그널: `presetLoadRequested()`, `addPathRequested(str)`, `autoOrganizeRequested()`

### `_GroupedScreen`, `_GroupedFolderCard`, `_FileIconCard`, `_InfoBanner` (`src/ui/views/organize_view.py`)
- 자동 그룹화 결과 화면과 폴더 카드 · 파일 아이콘 카드 · 안내 배너 구성 요소.

### `SearchView` (`src/ui/views/search_view.py`)
- 자연어 검색 화면. 채팅형 UI, 파일 첨부/드래그 앤 드롭, 백그라운드 검색, 결과 카드 표시, 탐색기 열기.

### `_FileResultCard` (`src/ui/views/search_view.py`)
- 검색 결과 한 건을 표시하는 카드 위젯.

### `SavedView` (`src/ui/views/saved_view.py`)
- 저장목록 화면. 셀 더블클릭으로 파일명·태그를 편집하고 저장 시 실제 파일 이름 변경과 DB 갱신을 수행한다.
- 편집 값은 `item.text()`로 얻은 Python `str`을 그대로 DB에 바인딩하며 별도의 `encode()`/`decode()` 변환을 하지 않아 한글이 유지된다.
- 핵심: `load_data()`, `save_to_db()`, `on_save_changes()`, `on_delete_file()`
