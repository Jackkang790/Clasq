# Clasq

Clasq는 로컬 AI를 이용해 파일을 검색하고, 정리 계획을 미리 확인한 뒤 안전하게 적용할 수 있는 Windows 데스크톱 애플리케이션입니다.

## 다운로드 및 설치

최신 빌드는 GitHub Releases에서 받을 수 있습니다.

- [Clasq v1.2.1 Release 페이지](https://github.com/Jackkang790/Clasq/releases/tag/v1.2.1)
- [Clasq_Setup_1.2.1.exe 직접 다운로드](https://github.com/Jackkang790/Clasq/releases/download/v1.2.1/Clasq_Setup_1.2.1.exe)

설치파일을 다운로드해 실행하면 됩니다. Python, pip, PySide6, Docker, WSL, llama.cpp, FFmpeg는 별도로 설치할 필요가 없습니다.

> production code signing이 적용되지 않았습니다. Windows SmartScreen 또는 보안 경고가 표시될 수 있습니다.

## 설치파일 무결성

`Clasq_Setup_1.2.1.exe` SHA-256:

```text
(빌드 후 갱신 예정)
```

## 지원 환경

- Windows x64
- NVIDIA GPU
- 로컬 AI 실행 (llama.cpp 내장)

## AI 모델

AI 모델은 설치파일에 포함되어 있지 않습니다. 모델이 없는 PC에서는 처음 AI 기능을 사용할 때 약 6.2GB 다운로드 안내가 표시되며, 사용자가 동의한 경우에만 다운로드합니다.

다운로드된 모델은 로컬 캐시에 보관되므로 유효한 캐시가 있으면 다시 다운로드하지 않습니다.
중단된 다운로드는 `.part` 파일에서 안전하게 이어받으며, 완료 크기와 SHA-256 검증을 통과한 파일만 모델로 사용합니다.

## 제거

Windows **설정 → 앱 → 설치된 앱 → Clasq → 제거** 또는 시작 메뉴의 **Clasq 제거**를 사용합니다.

- 기본 제거는 프로그램과 설치 바로가기만 삭제합니다. `%LOCALAPPDATA%\Clasq`의 AI 모델, 설정, 분석 DB, 로그 및 캐시는 재설치를 위해 유지됩니다.
- 제거 중 완전 삭제를 선택하면 Clasq가 기본 데이터 폴더에 생성한 모델과 사용자 데이터를 함께 삭제합니다.
- Clasq에 등록한 사진, 영상, 문서 및 작업 폴더의 원본 파일은 기본 제거와 완전 삭제 모두에서 삭제되지 않습니다.
- `CLASQ_MODEL_CACHE_DIR`로 별도 위치를 지정한 모델 캐시는 안전을 위해 완전 삭제에서도 자동 삭제하지 않습니다.

## 주요 기능

- 자연어 및 메타데이터 기반 파일 검색, 첨부파일 대화
- Analyze → Plan → Preview → Apply 순서의 안전한 파일 정리
- 충돌 방지, 명시적 승인, Undo 및 영구 정리 이력
- 미분류 파일 수동 태그 지정 (다중 선택, 파일 열기, 삭제)
- 저장 목록 편집 및 선택 삭제
- 로컬 Qwen 모델과 앱 소유 llama-server lifecycle

## v1.2.1 변경 사항

- Windows 시작 메뉴에 **Clasq 제거** 항목 추가, 기본 제거와 완전 삭제 선택 지원
- 기본 제거 시 AI 모델·설정·DB·로그 등 사용자 데이터 보존
- 사용자 원본 파일 및 외부 `CLASQ_MODEL_CACHE_DIR`은 어떤 제거 방식에서도 삭제하지 않음

## v1.2.0 변경 사항

- 2GB 이상 모델 다운로드 진행률의 음수/초과 표시 수정
- HTTP Range와 `Content-Range` 기반 안전한 모델 다운로드 재개
- 경로 추가 및 파일 변경 확인을 GUI worker에서 실행하고 실제 파일 단위 진행률·취소 지원
- 신규 폴더는 `size + mtime_ns`로 빠르게 등록하고 SHA-256은 분석·중복 확인 시 지연 계산
- 2,326개 재현 폴더 기준 UI-ready 약 3.46초 → 0.15초로 단축
- 권한 오류, 파일 삭제 race, Windows junction/reparse point 방어 강화

## v1.1.0 변경 사항

### 정리하기 탭
- 탭 전환 시 사이드바 활성 탭 강조 표시
- 파일 목록이 등록된 경로(managed_paths) 기준으로만 표시 — 정리 완료 파일이 목록에서 자동 제거
- 자동정리 반복 실행 시 이미 정리된 파일이 다시 대상으로 잡히던 버그 수정
- 정리 결과 저장 폴더 선택 문구 개선
- 미분류 파일에서 **수동 태그 지정** 다이얼로그 추가
  - Ctrl/Shift 다중 선택
  - 파일명 더블클릭 또는 버튼으로 실제 파일 열기
  - 선택 파일 삭제 (디스크 + DB 동시 제거)
- 수동 태그 적용 후 Preview가 즉시 태그 기반으로 재구성
- 정리 이력 다이얼로그 디자인 통일 및 Undo 버튼 잘림 수정

### AI 분석 / 태그
- AI 분석 실패 시 확장자 기반 폴백 태그가 저장된 파일도 성공으로 집계
- 설정 탭 태깅 다이얼로그에 취소 버튼 추가 (X 버튼으로도 중단 가능)

### Undo
- Undo 후 비어있는 폴더 자동 삭제

### 검색하기 탭
- 첨부파일 대화 중 종료 버튼이 입력창 위에 항상 고정 표시

### 소스 실행
- `dist/Clasq/_internal/runtime/llama-server.exe` 자동 탐색 — 별도 설치 없이 소스 실행 가능

## 개발 참고: 클래스별 기능 정리

## 진입점 / 공통

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `MainWindow` | `main.py` | 타이틀바·사이드바·각 뷰(`OrganizeView`, `SearchView`, `SavedView`, `SettingsView`)를 `QStackedWidget`으로 묶는 메인 윈도우. 탭 전환 시 `Sidebar.set_active()`로 활성 탭을 강조한다. |
| `OllamaManager` | `ollama_manager.py` | 로컬 Ollama 수명주기 관리. |
| `RefreshManager` | `src/ui/refresh_manager.py` | 뷰 간 데이터 갱신 신호 허브. |

## 코어 / 파이프라인

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `ClasqCore` | `src/utils/core.py` | 업로드·스캔·분석·정리 전체를 조율하는 코어. `process_file_upload()`에서 파일 내용 분석을 수행하고 실패 시 확장자 기반 폴백 태그를 사용한다. |
| `MainProcessor` | `src/utils/core.py` | `ClasqCore`를 상속한 배치/CLI 처리기. |
| `ExtensionTagger` | `src/utils/file_pipeline.py` | 내용 분석 실패 시 사용하는 확장자 폴백 태거. |
| `TextExtractor` | `src/utils/file_pipeline.py` | PDF/DOCX/XLSX/PPTX/HWP 등에서 본문 텍스트를 추출한다. |
| `FileAnalyzer` | `src/utils/file_pipeline.py` | 추출한 텍스트·이미지를 AI에 보내 태그/카테고리/설명을 생성한다. |
| `FileRegistryManager` | `src/utils/db_manager.py` | `file_manager.db` 접근 계층. 파일 등록, 중복 처리, 태그 갱신, 관리 경로 CRUD, organize_history 이력 관리. |
| `SearchQueryParser` | `src/utils/query_parser.py` | 자연어 검색 문장을 AI로 해석해 검색 조건으로 변환한다. |
| `SearchEngine` | `src/utils/search_engine.py` | 파싱된 조건으로 DB를 조회하고 결과를 점수순으로 정리한다. |

## 워커 (GUI 스레드 분리)

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `FolderScanAndTagWorker` | `src/utils/workers.py` | 폴더 스캔 + AI 태깅 백그라운드 워커. `request_stop()`으로 현재 파일 완료 후 중단 가능. DB 저장 성공(폴백 포함)을 기준으로 성공 집계. |
| `IncrementalInventoryWorker` | `src/utils/workers.py` | 파일 변경 여부 빠른 점검 워커. AI 분석 없이 stat/fingerprint만 확인한다. |
| `OrganizeApplyWorker` | `src/utils/workers.py` | 정리 Plan 실행. Preflight → 이동 → 실패 시 rollback → index 동기화 → organize_history 기록. |
| `OrganizeUndoWorker` | `src/utils/workers.py` | organize_history 기반 파일 이동 되돌리기. Undo 후 빈 폴더 자동 삭제. |
| `OllamaInitWorker` | `src/utils/workers.py` | Ollama 초기화 4단계 워커. |
| `QueryParseWorker` | `src/utils/workers.py` | 검색어 파싱 백그라운드 워커. |
| `QueryProcessWorker` | `src/ui/views/search_view.py` | 검색 화면 전용 워커. |

## 위젯 / 공통 UI

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `TaskProgressDialog` | `src/ui/widgets/progress_dialog.py` | 공용 진행 다이얼로그. `cancellable=True` 시 취소 버튼 표시, X 버튼으로도 `canceled` 시그널 발생. |
| `Sidebar` | `src/ui/components/side_bar.py` | 좌측 내비게이션. `set_active(index)`로 현재 탭 버튼을 강조한다. |
| `TitleBar` | `src/ui/components/title_bar.py` | 커스텀 타이틀바. |
| `FileUploadView` | `src/ui/widgets/fileupload_view.py` | 드래그&드롭 및 파일 선택 업로드 위젯. |

## 화면 뷰

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `SettingsView` | `src/ui/views/settings_view.py` | 경로 관리 및 파일 태깅 화면. 태깅 중 취소 가능. |
| `OrganizeView` | `src/ui/views/organize_view.py` | 정리 화면. managed_paths 기준 파일 표시, 자동정리, 수동 태그 지정, 정리 이력/Undo 제공. |
| `_FileTableScreen` | `src/ui/views/organize_view.py` | 정리 대상 파일 테이블 화면. |
| `_GroupedScreen` | `src/ui/views/organize_view.py` | 자동 그룹화 결과 미리보기. 미분류 카드에 수동 태그 지정 버튼 포함. |
| `_HistoryDialog` | `src/ui/views/organize_view.py` | 정리 이력 다이얼로그. 앱 디자인 통일, Undo 버튼 잘림 수정. |
| `SearchView` | `src/ui/views/search_view.py` | 자연어 검색 화면. 첨부파일 대화 종료 버튼 항상 하단 고정. |
| `SavedView` | `src/ui/views/saved_view.py` | 저장 목록 화면. 태그·이름 인라인 편집, 선택 삭제. |
