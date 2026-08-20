import os
import shutil
import subprocess
import time
import requests


class OllamaManager:

    # =====================================================
    # Ollama 설정
    # =====================================================

    OLLAMA_URL = "http://127.0.0.1:11434"

    # 사용할 AI 모델
    MODEL_NAME = "gemma3"

    # =====================================================
    # Ollama 실행 파일 찾기
    # =====================================================

    @classmethod
    def get_ollama_path(cls):

        # PATH에서 찾기
        path = shutil.which("ollama")

        if path:
            return path

        # Windows 기본 설치 위치
        possible_paths = [
            os.path.expandvars(
                r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
            ),
            os.path.expandvars(
                r"%LOCALAPPDATA%\Ollama\ollama.exe"
            ),
            r"C:\Program Files\Ollama\ollama.exe",
        ]

        for path in possible_paths:

            if os.path.exists(path):
                return path

        return None

    # =====================================================
    # 설치 여부
    # =====================================================

    @classmethod
    def is_installed(cls):

        return cls.get_ollama_path() is not None

    # =====================================================
    # Ollama 설치
    # =====================================================

    @classmethod
    def install(cls):

        print("[Ollama] 설치되어 있지 않습니다.")
        print("[Ollama] 설치를 시작합니다.")

        try:

            command = (
                "irm https://ollama.com/install.ps1 | iex"
            )

            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                check=True,
            )

            # 설치 완료 확인
            for _ in range(30):

                if cls.is_installed():

                    print("[Ollama] 설치 완료")

                    return True

                time.sleep(1)

            print(
                "[Ollama] 설치 후 실행 파일을 찾지 못했습니다."
            )

            return False

        except Exception as e:

            print(
                f"[Ollama] 설치 실패: {e}"
            )

            return False

    # =====================================================
    # 서버 실행 여부
    # =====================================================

    @classmethod
    def is_running(cls):

        try:

            response = requests.get(
                cls.OLLAMA_URL,
                timeout=2,
            )

            return response.status_code == 200

        except requests.RequestException:

            return False

    # =====================================================
    # 서버 실행
    # =====================================================

    @classmethod
    def start_server(cls):

        if cls.is_running():

            print(
                "[Ollama] 서버가 이미 실행 중입니다."
            )

            return True

        ollama_path = cls.get_ollama_path()

        if not ollama_path:

            print(
                "[Ollama] 실행 파일을 찾을 수 없습니다."
            )

            return False

        print(
            "[Ollama] 서버를 시작합니다."
        )

        try:

            creation_flags = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )

            subprocess.Popen(
                [
                    ollama_path,
                    "serve",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

        except Exception as e:

            print(
                f"[Ollama] 서버 실행 실패: {e}"
            )

            return False

        # 최대 30초 대기
        for _ in range(30):

            if cls.is_running():

                print(
                    "[Ollama] 서버 실행 완료"
                )

                return True

            time.sleep(1)

        print(
            "[Ollama] 서버 시작 시간 초과"
        )

        return False

    # =====================================================
    # 모델 존재 여부
    # =====================================================

    @classmethod
    def model_exists(cls):

        ollama_path = cls.get_ollama_path()

        if not ollama_path:
            return False

        try:

            result = subprocess.run(
                [
                    ollama_path,
                    "list",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                return False

            for line in result.stdout.splitlines():

                # gemma3 또는 gemma3:latest
                model_name = line.split()[0] if line.split() else ""

                if (
                    model_name == cls.MODEL_NAME
                    or model_name.startswith(
                        cls.MODEL_NAME + ":"
                    )
                ):

                    return True

            return False

        except Exception as e:

            print(
                f"[Ollama] 모델 확인 실패: {e}"
            )

            return False

    # =====================================================
    # 모델 다운로드
    # =====================================================

    @classmethod
    def download_model(cls):

        ollama_path = cls.get_ollama_path()

        if not ollama_path:
            return False

        print(
            f"[Ollama] {cls.MODEL_NAME} 다운로드 시작..."
        )

        try:

            result = subprocess.run(
                [
                    ollama_path,
                    "pull",
                    cls.MODEL_NAME,
                ],
                check=True,
            )

            print(
                f"[Ollama] {cls.MODEL_NAME} 다운로드 완료"
            )

            return result.returncode == 0

        except Exception as e:

            print(
                f"[Ollama] 모델 다운로드 실패: {e}"
            )

            return False

    # =====================================================
    # 공용 API 호출
    # =====================================================

    @classmethod
    def request(cls, endpoint, payload, timeout=120, base_url=None):
        """Ollama REST 호출의 단일 진입점입니다."""
        url = (base_url or cls.OLLAMA_URL).rstrip("/")
        response = requests.post(f"{url}/api/{endpoint.lstrip('/')}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response
    # =====================================================
    # ★ Ollama 실제 모델 테스트
    # =====================================================

    @classmethod
    def test_model(cls):

        print(
            "[Ollama] AI 모델 연결을 테스트합니다..."
        )

        try:

            response = requests.post(
                f"{cls.OLLAMA_URL}/api/generate",
                json={
                    "model": cls.MODEL_NAME,
                    "prompt": "OK라고만 답해주세요.",
                    "stream": False,
                },
                timeout=120,
            )

            response.raise_for_status()

            data = response.json()

            if "response" not in data:

                print(
                    "[Ollama] 모델 응답 형식이 올바르지 않습니다."
                )

                return False

            print(
                "[Ollama] AI 모델 테스트 성공"
            )

            return True

        except requests.RequestException as e:

            print(
                f"[Ollama] AI 모델 테스트 실패: {e}"
            )

            return False

        except Exception as e:

            print(
                f"[Ollama] 모델 테스트 중 오류: {e}"
            )

            return False

    # =====================================================
    # ★ 자연어 → AI 응답
    # =====================================================

    @classmethod
    def generate(cls, prompt, timeout=120):

        try:

            response = requests.post(
                f"{cls.OLLAMA_URL}/api/generate",
                json={
                    "model": cls.MODEL_NAME,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=timeout,
            )

            response.raise_for_status()

            data = response.json()

            result = data.get("response")

            if result is None:

                raise RuntimeError(
                    "Ollama 응답에 response 필드가 없습니다."
                )

            return result

        except requests.HTTPError as e:

            print(
                f"[Ollama] HTTP 오류: {e}"
            )

            print(
                f"[Ollama] 응답 내용: {response.text}"
            )

            raise

        except requests.RequestException as e:

            print(
                f"[Ollama] 연결 오류: {e}"
            )

            raise

    # =====================================================
    # 전체 초기화
    # =====================================================

    @classmethod
    def initialize(cls):

        print("=" * 50)
        print("Ollama 초기화 시작")
        print("=" * 50)

        # -------------------------------------------------
        # 1. Ollama 설치
        # -------------------------------------------------

        if not cls.is_installed():

            if not cls.install():

                return False

        # -------------------------------------------------
        # 2. Ollama 서버
        # -------------------------------------------------

        if not cls.start_server():

            return False

        # -------------------------------------------------
        # 3. 모델 확인
        # -------------------------------------------------

        if cls.model_exists():

            print(
                f"[Ollama] {cls.MODEL_NAME} 모델이 이미 존재합니다."
            )

        else:

            # -------------------------------------------------
            # 4. 모델 다운로드
            # -------------------------------------------------

            if not cls.download_model():

                return False

        # -------------------------------------------------
        # 5. 실제 API 테스트
        # -------------------------------------------------

        if not cls.test_model():

            return False

        print("=" * 50)
        print("Ollama 초기화 완료")
        print("=" * 50)

        return True