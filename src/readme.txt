feat: 백엔드 관제탑(MainProcessor) 도입 및 자연어 검색 파이프라인 통합

[주요 변경 사항]
- 아키텍처 개편: 
  - MainProcessor 관제탑 모듈 추가하여 파일 전처리, AI 분석, DB 저장을 비즈니스 로직으로 분리.
  - GUI 스레드와 백엔드 처리 로직을 완전히 격리하여 UI 응답성 개선.

- 자연어 파싱 및 검색 고도화:
  - QueryParseWorker, SearchEngine 도입으로 자연어 의도(@검색, @대화) 파싱 구현.
  - 지능형 검색 엔진 추가: 불용어(Stopwords) 필터링, 동의어(Synonym) 사전 확장, 검색 0건 방지용 폴백(Fallback) 검색 로직 구현.

- 안정성 보완:
  - 윈도우 경로 인코딩 정규화(￥ 처리 등) 및 경로 문제 근본 해결.
  - DB 초기화 로직 변경: os.remove 대신 SQL DELETE 방식을 적용하여 WinError 32(프로세스 점유 에러) 차단.

[모듈별 역할 정의]
- main_processor.py: 백엔드 관제탑. 시스템 전체 파이프라인의 핵심 로직을 조율하며, 분석된 파일을 데이터베이스(DB)로 자동 라우팅.
- file_pipeline.py: 문서/이미지/미디어 원문을 추출하는 전처리 엔진. 로컬 AI(Ollama)와 통신하여 메타데이터를 JSON 형태로 생성.
- query_parser.py: 자연어 입력을 분석하여 사용자의 의도(@검색, @대화)를 구조화된 JSON 데이터로 분류.
- search_engine.py: DB 조회, 검색 조건 필터링, 동의어 확장 및 검색 결과 폴백(Fallback) 처리 담당.

[팀원 B 역할 수행 요약 (Data Pre-processing & AI Intelligence)]
- 데이터 감각(Preprocessing): TextExtractor 및 FileAnalyzer를 통해 문서/이미지의 예외 상황을 원천 차단하는 방어적 프로그래밍 구현.
- AI 통신 규격화(Communication): 로컬 AI와 통신 시 'format: json' 강제 및 데이터 파싱 로직을 통해 정제된 구조화 데이터(JSON) 확보.
- 의도 해석(Parsing): 사용자의 자연어 입력을 분석하여 시스템이 즉시 실행 가능한 '의도(Intent)'와 '데이터'로 변환하는 지능형 파서 구현.

[용어 및 개념 설명]
- 자연어(Natural Language): 인간이 사용하는 일상 언어. 이를 '자연어 처리(NLP)'를 통해 컴퓨터가 다룰 수 있는 JSON 형식으로 변환합니다.
- 불용어(Stopwords): 검색 시 의미가 적은 단어(예: '파일', '찾아줘'). 검색 품질 향상을 위해 검색 전 단계에서 필터링합니다.
- 동의어 확장(Synonym Expansion): 검색 키워드('전쟁')를 관련 단어('대전', '전투')로 확장하여, 정보 누락을 방지하고 검색 적중률을 높이는 기법입니다.
- 폴백(Fallback) 검색: 검색 결과가 0건일 때, 시스템이 자동으로 검색 조건을 완화하여 연관 데이터를 찾아주는 안전장치입니다.

[시스템 아키텍처 및 요구 사항]
- 데이터 통신: 모든 AI 분석 결과 및 라우팅 메시지는 명확한 구조와 확장성을 보장하기 위해 'JSON' 규격으로 통신합니다.
- 필수 환경 설정(Dependencies):
  1. Ollama 설치: 텍스트 분석용 'qwen2.5:3b', 비전 모델 'llava' 모델 설치 필수.
  2. FFmpeg 설치: 미디어 파일(mp3, mp4 등) 음성 인식(STT)을 위한 필수 엔진 (시스템 환경 변수 등록 필요).
  3. Python 라이브러리: PySide6, pypdf, python-pptx, olefile, openai-whisper, requests, openpyxl, pillow 등 설치 필수.

[향후 고도화 방향 (팀원 협업 과제)]
- Frontend팀(A): 우리 파이프라인을 웹 API(FastAPI 등)로 확장하고, 대용량 처리를 위한 상세 상태 UI(진행률, 로그) 고도화.
- DB/Data팀(C): 파일 해시 기반 중복 감지 로직 강화, 대량 트랜잭션 시 무결성 보장 및 물리적 파일 동기화 정책 고도화.

[기타]
- 전체 코드 주석 보강 및 가독성 최적화 완료.