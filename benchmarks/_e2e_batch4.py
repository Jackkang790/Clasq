"""Batch 4-D E2E test — real files, no code modification."""
import os, glob, sqlite3, tempfile, json
from pathlib import Path

# ── 테스트 파일 경로 탐색 ──────────────────────────────────────────────
conn = sqlite3.connect('file_manager.db')
rows = conn.execute("SELECT file_name, file_path FROM files").fetchall()
conn.close()

img_path = doc_path = None
for name, path in rows:
    ext = os.path.splitext(name)[1].lower()
    if not os.path.isfile(path):
        continue
    if ext in ('.png', '.jpg', '.jpeg') and not img_path:
        img_path = path
    if ext in ('.pdf', '.pptx', '.docx', '.txt') and not doc_path:
        doc_path = path
    if img_path and doc_path:
        break

vids = glob.glob(os.path.expanduser('~/Videos/**/*.mp4'), recursive=True)
vid_path = min(vids, key=os.path.getsize) if vids else None

print('=' * 60)
print('Batch 4-D  E2E Test  (real files, no code changes)')
print('=' * 60)
print(f'IMAGE: {os.path.basename(img_path)}  ({os.path.getsize(img_path):,}B)')
print(f'DOC  : {os.path.basename(doc_path)}  ({os.path.getsize(doc_path):,}B)')
print(f'VIDEO: {os.path.basename(vid_path)}  ({os.path.getsize(vid_path):,}B)')
print()

from src.utils.core import ClasqCore
from src.utils.file_pipeline import TextExtractor, FileAnalyzer
from src.ai.video_analyzer import VideoAnalyzer, FFmpegNotFoundError, FFmpegExecutionError

core = ClasqCore(db_path='file_manager.db')
te = TextExtractor()
va = VideoAnalyzer()

PASS = 'PASS'
FAIL = 'FAIL'
results = {}

# ── TEST 1: 이미지 ─────────────────────────────────────────────────────
print('[TEST 1] Image: file read → ImageAnalyzer(Qwen) → response')

img_bytes, img_status = te.process_image(img_path)
print(f'  Step1 TextExtractor.process_image: {img_status}  {len(img_bytes):,}B')

result_img = core.process_file_upload(img_path)
atype = result_img.get('@TYPE')
status = result_img.get('status')
meta = result_img.get('metadata', {})
db_ok = result_img.get('db_result', {}).get('success', False)
print(f'  Step2 process_file_upload: @TYPE={atype}  status={status}  db_saved={db_ok}')
print(f'  error: {str(result_img.get("error",""))[:90]}')

t1_extract = img_status == 'SUCCESS' and len(img_bytes) > 0
t1_format = (
    atype == '@DB'
    and 'display_name' in meta
    and 'tags' in meta
    and 'ai_comment' in meta
)
results['image'] = {
    'step1_extraction': t1_extract,
    'response_format': t1_format,
    'ai_status': status,
}
print(f'  STEP1 (image read/validate): {PASS if t1_extract else FAIL}')
print(f'  response format (@TYPE=@DB + metadata): {PASS if t1_format else FAIL}')
print(f'  Qwen analysis: {status}  (FAILED = expected without llama-server)')
print()

# ── TEST 2: 문서 ─────────────────────────────────────────────────────
print('[TEST 2] Document: text extraction → QwenClient → response')

doc_text, doc_status = te.extract(doc_path)
print(f'  Step1 TextExtractor.extract: {doc_status}  {len(doc_text):,} chars')

result_doc = core.process_file_upload(doc_path)
atype2 = result_doc.get('@TYPE')
status2 = result_doc.get('status')
meta2 = result_doc.get('metadata', {})
db_ok2 = result_doc.get('db_result', {}).get('success', False)
print(f'  Step2 process_file_upload: @TYPE={atype2}  status={status2}  db_saved={db_ok2}')
print(f'  error: {str(result_doc.get("error",""))[:90]}')

t2_extract = doc_status == 'SUCCESS' and len(doc_text) > 0
t2_format = (
    atype2 == '@DB'
    and 'display_name' in meta2
    and 'tags' in meta2
    and 'ai_comment' in meta2
)
results['doc'] = {
    'step1_extraction': t2_extract,
    'response_format': t2_format,
    'ai_status': status2,
}
print(f'  STEP1 (text extraction): {PASS if t2_extract else FAIL}  ({len(doc_text)} chars)')
print(f'  response format (@TYPE=@DB + metadata): {PASS if t2_format else FAIL}')
print(f'  Qwen analysis: {status2}  (FAILED = expected without llama-server)')
print()

# ── TEST 3: 영상 ─────────────────────────────────────────────────────
print('[TEST 3] Video: FFmpeg frame extraction → VideoAnalyzer(Qwen) → response')

ffmpeg_path = va.find_ffmpeg()
print(f'  ffmpeg: {ffmpeg_path}')

t3_ffmpeg = False
with tempfile.TemporaryDirectory() as tmpdir:
    try:
        frames, timestamps = va.extract_representative_frames(vid_path, Path(tmpdir))
        print(f'  Step1 FFmpeg: {len(frames)} frames  ts={[round(t,1) for t in timestamps[:4]]}')
        t3_ffmpeg = len(frames) > 0
    except (FFmpegNotFoundError, FFmpegExecutionError) as exc:
        print(f'  Step1 FFmpeg error: {exc}')

result_vid = core.process_file_upload(vid_path)
atype3 = result_vid.get('@TYPE')
status3 = result_vid.get('status')
meta3 = result_vid.get('metadata', {})
db_ok3 = result_vid.get('db_result', {}).get('success', False)
print(f'  Step2 process_file_upload: @TYPE={atype3}  status={status3}  db_saved={db_ok3}')
print(f'  error: {str(result_vid.get("error",""))[:100]}')

t3_format = (
    atype3 == '@DB'
    and 'display_name' in meta3
    and 'tags' in meta3
    and 'ai_comment' in meta3
)
results['video'] = {
    'step1_ffmpeg': t3_ffmpeg,
    'response_format': t3_format,
    'ai_status': status3,
}
print(f'  STEP1 (FFmpeg frame extraction): {PASS if t3_ffmpeg else FAIL}')
print(f'  response format (@TYPE=@DB + metadata): {PASS if t3_format else FAIL}')
print(f'  Qwen analysis: {status3}  (FAILED = expected without llama-server)')
print()

# ── TEST 4: 결과 UI 흐름 전달 ─────────────────────────────────────────
print('[TEST 4] Response format passes correctly to UI flow')
ui_ok = True
for label, res in [('IMAGE', result_img), ('DOC', result_doc), ('VIDEO', result_vid)]:
    m = res.get('metadata', {})
    ok = (
        res.get('@TYPE') == '@DB'
        and res.get('status') in ('SUCCESS', 'FAILED')
        and 'display_name' in m
        and 'tags' in m
        and 'ai_comment' in m
        and isinstance(m.get('tags'), list)
    )
    print(f'  {label}: @TYPE={res.get("@TYPE")} status={res.get("status")} '
          f'tags_type={type(m.get("tags")).__name__}  {PASS if ok else FAIL}')
    if not ok:
        ui_ok = False
print(f'  UI response format: {PASS if ui_ok else FAIL}')
print()

# ── TEST 5: AI 없이 파일관리/검색 ─────────────────────────────────────
print('[TEST 5] File management / search works without AI')
from src.utils.search_engine import SearchEngine

se = SearchEngine('file_manager.db')
sr = se.process_query_result({
    '@TYPE': '@검색', 'query_keywords': ['보고서'], 'target_extension': []
})
print(f'  keyword search (보고서): {sr["action"]}  {len(sr["data"])} results')
search_ok = sr['action'] == 'UPDATE_TABLE' and len(sr['data']) > 0

stats = core.get_db_stats()
print(f'  get_db_stats: total_files={stats["total_files"]}')
stats_ok = stats['total_files'] > 0

organized = core.get_files_for_organize()
print(f'  get_files_for_organize: {len(organized)} files')
print(f'  file management + search: {PASS if (search_ok and stats_ok) else FAIL}')
print()

# ── 최종 판정 ─────────────────────────────────────────────────────────
print('=' * 60)
print('FINAL RESULT')
print('=' * 60)

checks = [
    ('Image  Step1: file read / validate',        t1_extract),
    ('Image  response format (@TYPE + metadata)',  t1_format),
    ('Doc    Step1: text extraction',              t2_extract),
    ('Doc    response format (@TYPE + metadata)',  t2_format),
    ('Video  Step1: FFmpeg frame extraction',      t3_ffmpeg),
    ('Video  response format (@TYPE + metadata)',  t3_format),
    ('UI flow: response format correct',           ui_ok),
    ('No-AI: search / file management works',      search_ok and stats_ok),
]

all_pass = True
for label, ok in checks:
    flag = PASS if ok else FAIL
    if not ok:
        all_pass = False
    print(f'  {flag:4}  {label}')

print()
print('Qwen analysis (requires llama-server + model):')
print(f'  Image : {results["image"]["ai_status"]}')
print(f'  Doc   : {results["doc"]["ai_status"]}')
print(f'  Video : {results["video"]["ai_status"]}')
print()
verdict = 'PASS' if all_pass else 'FAIL'
print(f'Batch 4-D verdict: {verdict}')
