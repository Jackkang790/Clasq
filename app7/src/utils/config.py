"""Clasq에서 공유하는 실행 설정과 지원 파일 형식."""

OLLAMA_URL = "http://127.0.0.1:11434"
TEXT_MODEL = "gemma3"
VISION_MODEL = "llava"

MAX_ANALYSIS_CHARS = 2000
MAX_DOCUMENT_SIZE_MB = 50
MAX_IMAGE_SIZE_MB = 100

TEXT_EXTENSIONS = (
    ".txt", ".csv", ".json", ".xml", ".yaml", ".yml", ".html", ".htm",
    ".md", ".markdown",
)
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".pptx", ".hwp", ".hwpx")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif")
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS
