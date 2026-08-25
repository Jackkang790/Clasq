# Clasq

Clasq는 로컬 AI를 이용해 파일을 검색하고, 정리 계획을 미리 확인한 뒤 안전하게 적용할 수 있는 Windows 데스크톱 애플리케이션입니다.

## 다운로드 및 설치

최신 점검용 빌드는 GitHub Releases에서 받을 수 있습니다.

- [Clasq v1.0.0 Release 페이지](https://github.com/Jackkang790/Clasq/releases/tag/v1.0.0)
- [Clasq_Setup_1.0.0.exe 직접 다운로드](https://github.com/Jackkang790/Clasq/releases/download/v1.0.0/Clasq_Setup_1.0.0.exe)
- [SHA-256 체크섬](https://github.com/Jackkang790/Clasq/releases/download/v1.0.0/Clasq_Setup_1.0.0.exe.sha256.txt)

설치파일을 다운로드해 실행하면 됩니다. Python, pip, PySide6, Docker, WSL, llama.cpp, FFmpeg는 별도로 설치할 필요가 없습니다.

> 현재 v1.0.0은 점검용 prerelease이며 production code signing이 적용되지 않았습니다. Windows SmartScreen 또는 보안 경고가 표시될 수 있습니다.

## 지원 환경

- Windows x64
- NVIDIA GPU
- 로컬 AI 실행

## AI 모델

AI 모델은 설치파일에 포함되어 있지 않습니다. 모델이 없는 PC에서는 처음 AI 기능을 사용할 때 약 6.2GB 다운로드 안내가 표시되며, 사용자가 동의한 경우에만 다운로드합니다.

다운로드된 모델은 로컬 캐시에 보관되므로 유효한 캐시가 있으면 다시 다운로드하지 않습니다.

## 주요 기능

- 자연어 및 메타데이터 기반 파일 검색
- Analyze → Plan → Preview → Apply 순서의 안전한 파일 정리
- 충돌 방지, 명시적 승인, 실행 취소 및 영구 이력
- 저장 목록 편집, 선택 삭제 및 AI 태깅
- 로컬 Qwen 모델과 앱 소유 llama-server lifecycle

## 설치파일 무결성

`Clasq_Setup_1.0.0.exe`의 SHA-256:

```text
EB6354B1EF522695746AD0F89385094D299A105B688600F9005A87641B731882
```

## 개발 참고: 클래스별 기능 정리

## 진입점 / 공통

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `MainWindow` | `main.py` | 타이틀바·사이드바·각 뷰(`OrganizeView`, `SearchView`, `SavedView`, `SettingsView`)를 `QStackedWidget`으로 묶는 메인 윈도우. 사이드바 선택에 따라 화면을 전환하고 창 이동·리사이즈를 처리한다. |
| `OllamaManager` | `ollama_manager.py` | 로컬 Ollama 수명주기 관리. 설치 확인/설치, 서버 기동, 모델 존재 확인·다운로드, 모델 응답 테스트, 텍스트/비전 모델 REST 호출을 담당한다. |
| `RefreshManager` | `src/ui/refresh_manager.py` | 뷰 간 데이터 갱신 신호 허브. 파일 태깅·정리·수정 후 다른 화면이 DB를 다시 읽도록 시그널을 전달한다. |

## 코어 / 파이프라인

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `ClasqCore` | `src/utils/core.py` | 업로드·스캔·분석·정리 전체를 조율하는 코어. `process_file_upload()`에서 **파일 내용 분석을 먼저 수행**하고 실패한 경우에만 확장자 기반 폴백 태그를 사용한다. 디렉터리 스캔, 정리 미리보기, 실제 파일 이동, 저장 목록 갱신을 제공한다. |
| `MainProcessor` | `src/utils/core.py` | `ClasqCore`를 상속한 배치/CLI 성격의 처리기. 여러 파일을 순회 처리한다. |
| `FilePreprocessError` | `src/utils/file_pipeline.py` | 파일 전처리(열기·변환·추출) 실패를 나타내는 예외. |
| `ExtensionTagger` | `src/utils/file_pipeline.py` | **내용 분석 실패 시에만** 사용하는 확장자 폴백 태거. 이미지→`이미지`, 애니메이션 GIF→`움짤`, 텍스트→`텍스트`, 문서→`문서`, `.ppt/.pptx`→`ppt`, 그 외→`미분류`를 반환한다. |
| `TextExtractor` | `src/utils/file_pipeline.py` | PDF/DOCX/XLSX/PPTX/HWP/텍스트 등에서 본문 텍스트를 추출하고 성공/실패 상태를 함께 반환한다. |
| `FileAnalyzer` | `src/utils/file_pipeline.py` | 추출한 텍스트·이미지를 Ollama에 보내 태그/카테고리/설명을 생성한다. 분석 실패 시 `_build_fallback_response()`로 `ExtensionTagger` 태그를 붙인 응답을 만든다. |
| `FileRegistryManager` | `src/utils/db_manager.py` | `file_manager.db` 접근 계층. `files`/`managed_paths` 스키마 생성·마이그레이션, 파일 등록, 중복 해시 처리, 태그·메타데이터 갱신, 관리 경로 CRUD를 담당한다. 한글은 별도 인코딩 변환 없이 파이썬 `str` 그대로 저장·조회한다. |
| `SearchQueryParser` | `src/utils/query_parser.py` | 자연어 검색 문장을 AI로 해석해 태그·카테고리·기간 등 검색 조건으로 변환한다. |
| `SearchEngine` | `src/utils/search_engine.py` | 파싱된 조건으로 DB를 조회하고 검색 결과를 점수순으로 정리한다. |

## 워커 (GUI 스레드 분리)

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `FolderScanAndTagWorker` | `src/utils/workers.py` | 폴더 스캔 + AI 태깅을 백그라운드에서 수행. `progress`(문구)와 `fileProgress(현재, 전체, 파일명)` 시그널로 실제 처리 개수를 전달하며 GUI 객체를 직접 건드리지 않는다. |
| `OllamaInitWorker` | `src/utils/workers.py` | 설치 확인 → 서버 기동 → 모델 확인/다운로드 → 응답 테스트의 4단계 Ollama 초기화를 수행하고 `progress(단계, 전체, 상태)` / `completed(성공, 메시지)`를 emit 한다. |
| `QueryParseWorker` | `src/utils/workers.py` | 검색어 파싱을 백그라운드에서 실행한다. |
| `QueryProcessWorker` | `src/ui/views/search_view.py` | 검색 화면 전용 워커로 질의 파싱과 검색 실행을 이어서 처리한다. |

## 위젯 / 공통 UI

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `TaskProgressDialog` | `src/ui/widgets/progress_dialog.py` | 제목·상태 문구·Progress Bar·`n / m 처리 중`·현재 항목명을 함께 보여주는 공용 모달 다이얼로그. 전체 개수를 모를 때만 무한 진행(indeterminate)으로 동작한다. 파일 태깅과 Ollama 로딩에서 재사용한다. |
| `Sidebar` | `src/ui/components/side_bar.py` | 좌측 내비게이션. 화면 전환 시그널을 발생시킨다. |
| `TitleBar` | `src/ui/components/title_bar.py` | 커스텀 타이틀바(최소화/최대화/닫기, 드래그 이동). |
| `_AnimatedIconButton` | `src/ui/components/title_bar.py` | 타이틀바용 호버 애니메이션 아이콘 버튼. |
| `FileUploadView` | `src/ui/widgets/fileupload_view.py` | 드래그&드롭 및 파일 선택 업로드 위젯. |
| `_make_btn()` (함수) | `src/ui/views/organize_view.py` | 정리 화면의 공용 버튼 팩토리. `primary`는 보라색 강조 버튼, `danger`는 `setting_view`의 경로삭제 버튼과 동일한 `delRoot` 스타일(빨간색, hover/pressed/disabled 포함)을 적용한다. |

## 화면 뷰

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `SettingsView` | `src/ui/views/settings_view.py` | 경로 관리와 **파일 태깅의 소유 화면**. 경로 추가/삭제, 프리셋 저장(`assets/preset.json`), 태깅 실행 시 `TaskProgressDialog`를 띄우고 `FolderScanAndTagWorker`의 진행 시그널로 실제 진행률을 갱신하며 완료·오류 시 자동으로 닫는다. |
| `CheckBoxHeader` | `src/ui/views/settings_view.py` | 경로 테이블의 전체 선택 체크박스 헤더. |
| `OrganizeView` | `src/ui/views/organize_view.py` | 정리 화면. 버튼 배치는 `[프리셋 불러오기] [경로 추가] [경로 삭제] [자동정리]`이다. **프리셋은 `assets/preset.json`에서만** 읽고(`_read_presets`), 선택한 프리셋의 `targets`를 `type`(file/folder)·`path`·`extensions`에 따라 정리 대상에 반영한다(`_apply_preset`). `_on_remove_path()`는 선택한 항목을 **현재 정리 대상 목록에서만** 제외하며 DB·`preset.json`·실제 파일은 건드리지 않고, 선택이 없으면 `삭제할 경로를 선택해주세요.` 안내만 보여준다. `_on_auto_organize()`는 현재 테이블에 남아 있는 경로만 정리 대상으로 삼는다. 파일 없음/JSON 오류 시에도 예외 없이 `프리셋 없음` 또는 안내 메시지를 보여준다. |
| `_FileTableScreen` | `src/ui/views/organize_view.py` | 정리 대상 파일 테이블 화면. `presetLoadRequested`, `addPathRequested`, `removePathRequested`, `autoOrganizeRequested` 시그널을 발생시키고, `selected_rows()` / `remove_rows()`로 기존 다중 선택 방식을 그대로 쓴 행 제거를 제공한다. |
| `_GroupedScreen` | `src/ui/views/organize_view.py` | 자동 그룹화 결과 미리보기 화면. |
| `_GroupedFolderCard` | `src/ui/views/organize_view.py` | 그룹(폴더) 단위 카드 위젯. |
| `_FileIconCard` | `src/ui/views/organize_view.py` | 파일 종류별 아이콘 카드. |
| `_InfoBanner` | `src/ui/views/organize_view.py` | 상단 안내 배너. |
| `SearchView` | `src/ui/views/search_view.py` | 자연어 검색 화면. 질의 입력·결과 카드 표시·파일 열기를 담당한다. |
| `_FileResultCard` | `src/ui/views/search_view.py` | 검색 결과 한 건을 보여주는 카드. |
| `SavedView` | `src/ui/views/saved_view.py` | 저장 목록 화면. 더블클릭 인라인 편집으로 이름·태그·설명을 수정하며, 한글 값은 인코딩 변환 없이 DB에 그대로 반영·재조회된다. |
