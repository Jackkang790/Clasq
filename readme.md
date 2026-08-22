# 클래스별 기능 정리

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

## 화면 뷰

| 클래스 | 파일 | 역할 |
| --- | --- | --- |
| `SettingsView` | `src/ui/views/settings_view.py` | 경로 관리와 **파일 태깅의 소유 화면**. 경로 추가/삭제, 프리셋 저장(`assets/preset.json`), 태깅 실행 시 `TaskProgressDialog`를 띄우고 `FolderScanAndTagWorker`의 진행 시그널로 실제 진행률을 갱신하며 완료·오류 시 자동으로 닫는다. |
| `CheckBoxHeader` | `src/ui/views/settings_view.py` | 경로 테이블의 전체 선택 체크박스 헤더. |
| `OrganizeView` | `src/ui/views/organize_view.py` | 정리 화면. `[프리셋 불러오기] [경로 추가하기] [자동 정리하기]` 버튼 배치를 유지하며, **프리셋은 `assets/preset.json`에서만** 읽는다(`_read_presets`). 선택한 프리셋의 `targets`를 `type`(file/folder)·`path`·`extensions`에 따라 정리 대상 목록에 반영하고(`_apply_preset`), 파일 없음/JSON 오류 시에도 예외 없이 `프리셋 없음` 또는 안내 메시지를 보여준다. |
| `_FileTableScreen` | `src/ui/views/organize_view.py` | 정리 대상 파일 테이블 화면. `presetLoadRequested`, `addPathRequested`, `autoOrganizeRequested` 시그널을 발생시킨다. |
| `_GroupedScreen` | `src/ui/views/organize_view.py` | 자동 그룹화 결과 미리보기 화면. |
| `_GroupedFolderCard` | `src/ui/views/organize_view.py` | 그룹(폴더) 단위 카드 위젯. |
| `_FileIconCard` | `src/ui/views/organize_view.py` | 파일 종류별 아이콘 카드. |
| `_InfoBanner` | `src/ui/views/organize_view.py` | 상단 안내 배너. |
| `SearchView` | `src/ui/views/search_view.py` | 자연어 검색 화면. 질의 입력·결과 카드 표시·파일 열기를 담당한다. |
| `_FileResultCard` | `src/ui/views/search_view.py` | 검색 결과 한 건을 보여주는 카드. |
| `SavedView` | `src/ui/views/saved_view.py` | 저장 목록 화면. 더블클릭 인라인 편집으로 이름·태그·설명을 수정하며, 한글 값은 인코딩 변환 없이 DB에 그대로 반영·재조회된다. |
