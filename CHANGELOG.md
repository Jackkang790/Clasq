# Changelog

모든 주요 변경 사항을 이 파일에 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 따릅니다.

---

## [1.2.1] - 2026-08-27

### 추가
- Windows 시작 메뉴에 **Clasq 제거** 항목 추가
- 제거 시 **기본 제거**와 **완전 삭제** 선택 지원

### 개선
- 기본 제거: AI 모델, 설정, DB, 로그 등 사용자 데이터 보존
- `/DELETEUSERDATA` 지정 시에만 `%LOCALAPPDATA%\Clasq` 완전 삭제
- 사용자 원본 파일 및 외부 `CLASQ_MODEL_CACHE_DIR`은 어떤 제거 방식에서도 자동 삭제하지 않음
- 실행 중 제거 시 Clasq 프로세스 및 Clasq-owned llama-server lifecycle 처리 보강 (Inno Restart Manager)

### 검증
- 실제 Inno Setup build/install/uninstall: PASS
- installer/lifecycle 관련 테스트: 12 passed

---

## [1.2.0] - 2026-08-27

### 변경
- 경로 추가 시 신규 파일 전체 SHA-256 선계산을 제거하고 `size + mtime_ns` 기반 경량 fingerprint로 즉시 등록
- SHA-256은 AI 분석, 중복 판별 등 정밀 파일 동일성이 필요한 시점에 지연 계산
- 파일 inventory 등록을 단일 worker transaction으로 묶어 대량 폴더 등록 성능 개선
- 모델 다운로드 진행 bar를 Qt 32-bit 범위와 무관한 정규화 값으로 표시

### 수정
- 2GB 이상 모델 다운로드 진행 byte가 음수가 되거나 전체 크기가 32-bit wrap되던 문제
- resume 응답의 `Content-Length`를 전체 모델 크기로 오인할 수 있던 문제
- Range 요청을 서버가 무시했을 때 기존 partial 뒤에 전체 파일을 append할 수 있던 문제
- 2,326개 이상 파일을 확인한 뒤 GUI thread에서 전체 hash와 DB 등록을 반복해 응답 없음이 발생하던 문제
- 파일 검사 중 권한 오류, 삭제 race, junction 및 취소 처리 보강

### 검증
- 2,326개 재현 폴더에서 UI-ready 3.463초 → 0.154초, UI-ready 이전 SHA-256 2,326회 → 0회
- 10,000개 작은 파일 metadata-only 등록 및 5GB sparse file eager-hash 방지 검증

---

## [1.1.0] - 2026-08-26

### 추가
- 탭 전환 시 사이드바 활성 탭 강조 표시 (`Sidebar.set_active()`)
- 정리하기 탭 미분류 카드에 **수동 태그 지정** 다이얼로그
  - Ctrl/Shift 다중 선택 후 태그 일괄 적용
  - 파일명 더블클릭 또는 버튼으로 실제 파일 열기
  - 선택 파일 삭제 (디스크 + DB + 인덱스 동시 제거)
- `FolderScanAndTagWorker.request_stop()` — 현재 파일 완료 후 태깅 중단 가능
- `IncrementalInventoryWorker` — AI 없이 stat/fingerprint만 확인하는 빠른 점검 워커
- 설정 탭 태깅 다이얼로그 취소 버튼 (X 버튼으로도 중단 가능)
- Undo 후 비어있는 폴더 자동 삭제
- 소스 실행 시 `dist/Clasq/_internal/runtime/llama-server.exe` 자동 탐색

### 변경
- 정리하기 탭 파일 목록이 `managed_paths` 기준으로만 표시 — 정리 완료 파일 자동 제거
- `_get_target_folders()` 가 테이블 행 대신 `managed_paths` DB를 사용 — 재정리 루프 방지
- AI 분석 실패 시 확장자 기반 폴백 태그 저장 파일도 성공으로 집계
- 정리 이력 다이얼로그 디자인을 앱 스타일로 통일
- 수동 태그 다이얼로그 디자인을 앱 스타일로 통일
- 정리 결과 저장 폴더 선택 문구 개선 ("정리할 기본 폴더 선택" → "정리 결과를 저장할 폴더 선택")
- 첨부파일 대화 종료 버튼이 입력창 위에 항상 고정 표시
- `TaskProgressDialog` X 버튼 클릭 시 `canceled` 시그널 발생
- 저장목록 탭에서 미태깅 전체 AI 태깅 버튼 제거

### 수정
- 수동 태그 지정 후 Preview가 태그 기반으로 즉시 재구성되지 않던 문제
- 모든 파일이 미분류일 때 자동정리 이후 `_preview_base_path`가 설정되지 않아 수동 태그가 반영되지 않던 문제
- Undo 버튼이 이력 다이얼로그에서 위아래로 잘리던 문제
- 설정 탭 AI 분석 완료 후 ETA가 초기화되던 문제
- 설정 홈 단축키 복원

---

## [1.0.0] - 2026-06-01

### 추가
- 최초 릴리즈
- 자연어 및 메타데이터 기반 파일 검색
- Analyze → Plan → Preview → Apply 순서의 안전한 파일 정리
- 충돌 방지, 명시적 승인, Undo 및 영구 정리 이력
- 저장 목록 편집 및 선택 삭제
- 검색 탭 파일 첨부 대화
- 로컬 Qwen 모델 + llama-server 내장

---

[1.2.1]: https://github.com/Jackkang790/Clasq/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Jackkang790/Clasq/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Jackkang790/Clasq/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Jackkang790/Clasq/releases/tag/v1.0.0
