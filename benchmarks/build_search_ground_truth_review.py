"""Build a human-reviewable, weakly-labelled search-quality CSV.

This benchmark is read-only. It never marks a row verified and never copies
document bodies into the output.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.views.search_view import SearchView
from src.utils.search_engine import SearchEngine


@dataclass(frozen=True)
class ReviewSpec:
    id: str
    query: str
    category: str
    difficulty: str
    selector: str
    status: str
    note: str


SPECS = (
    ReviewSpec("A01", "네트워크 기초 피피티 찾아줘", "file_type_topic", "easy", "1장 패킷트레이서와 네트워크 기초", "candidate", "주제와 형식이 파일명에 가깝다."),
    ReviewSpec("A02", "창업 관련 PDF 보여줘", "file_type_topic", "medium", "창업경진대회&&.pdf", "ambiguous", "복수 PDF가 관련된다."),
    ReviewSpec("A03", "병원 모니터링 마크다운 찾아줘", "file_type_topic", "easy", "pencil-prompt-hospital-monitoring", "candidate", "병원 모니터링 MD."),
    ReviewSpec("A04", "리눅스 설치 피피티 보여줘", "file_type_topic", "easy", "ch02&&리눅스 설치", "candidate", "강의 제목이 명확하다."),
    ReviewSpec("A05", "정보처리기사 PDF 찾아줘", "file_type_topic", "easy", "정보처리기사&&.pdf", "ambiguous", "여러 회차가 있다."),
    ReviewSpec("A06", "스마트팜 발표자료 찾아줘", "file_type_topic", "medium", "스마트팜&&.pptx", "ambiguous", "버전이 복수다."),
    ReviewSpec("A07", "로보독 사업계획 피피티 찾아줘", "file_type_topic", "easy", "lobodoc&&사업계획", "ambiguous", "사업계획서 버전이 복수다."),
    ReviewSpec("A08", "빅데이터 분석기사 PDF 보여줘", "file_type_topic", "easy", "빅데이터분석기사&&.pdf", "ambiguous", "기출/요약 PDF가 복수다."),
    ReviewSpec("A09", "병원 환자 화면 이미지 찾아줘", "file_type_topic", "medium", "04-patient-detail.png", "candidate", "환자 상세 UI."),
    ReviewSpec("A10", "우체국 위치 엑셀 파일 찾아줘", "file_type_topic", "easy", "우체국위치&&.xlsx", "candidate", "위치 XLSX."),
    ReviewSpec("B01", "로보독 피치 자료 찾아줘", "filename_meaning", "easy", "lobodoc&&피치", "ambiguous", "피치덱 버전이 복수다."),
    ReviewSpec("B02", "스마트케어 화면 설계 자료 보여줘", "filename_meaning", "medium", "37_5_smartcare_panel", "ambiguous", "한영 표기가 다르다."),
    ReviewSpec("B03", "네트워크 장비 구성 강의 찾아줘", "filename_meaning", "medium", "2장 네트워크 구성요소", "candidate", "구성요소를 자연어로 바꾄다."),
    ReviewSpec("B04", "리눅스 복구 방법 강의자료 찾아줘", "filename_meaning", "medium", "ch06&&응급 복구", "candidate", "응급 복구 강의."),
    ReviewSpec("B05", "자동차 리콜 추세 보고서 찾아줘", "filename_meaning", "medium", "자동차 리콜 통계 트랜드 리포트", "candidate", "추세/트랜드 표현 차이."),
    ReviewSpec("B06", "AI 박람회 참관 문서 보여줘", "filename_meaning", "medium", "AI_EXPO_KOREA_2026_참관보고서", "ambiguous", "DOCX/PDF가 있다."),
    ReviewSpec("B07", "웹 화면 와이어프레임 자료 찾아줘", "filename_meaning", "easy", "웹_와이어프레임", "ambiguous", "버전이 복수다."),
    ReviewSpec("B08", "정보처리기사 SQL 문제 자료 찾아줘", "filename_meaning", "medium", "정보처리기사&&sql", "ambiguous", "문제/정답 버전이 있다."),
    ReviewSpec("C01", "창업대회 제출 서류 찾아줘", "path_project", "medium", "창업경진대회 참가신청서", "ambiguous", "제출 양식 전체."),
    ReviewSpec("C02", "프로그래밍 언어 정답 자료 보여줘", "path_project", "easy", "프로그래밍 언어 문제 정답", "ambiguous", "언어별 하위 문서."),
    ReviewSpec("C03", "창업 폴더에 있는 로보독 발표자료 찾아줘", "path_project", "easy", "창업_ppt&&lobodoc", "candidate", "경로와 프로젝트명이 단서다."),
    ReviewSpec("C04", "export 폴더의 웹 디자인 자료 보여줘", "path_project", "easy", "export&&웹_", "ambiguous", "export 산출물."),
    ReviewSpec("C05", "우체국 과제 파일 찾아줘", "path_project", "medium", "07-07 과제", "ambiguous", "과제 트리 전체."),
    ReviewSpec("C06", "공공 자동화 과제 문서 찾아줘", "path_project", "hard", "07-02 과제&&공공", "ambiguous", "상위 경로 의미."),
    ReviewSpec("D01", "기본 게이트웨이 설정 설명 자료 찾아줘", "body_text", "medium", "2장 네트워크 구성요소", "candidate", "본문 핵심 표현."),
    ReviewSpec("D02", "맥 주소 테이블 동작 설명 찾아줘", "body_text", "medium", "4장 스위치", "candidate", "MAC/맥 표기 차이."),
    ReviewSpec("D03", "서브넷 마스크 계산 자료 보여줘", "body_text", "medium", "2장 네트워크 구성요소||3장 라우터와 라우팅", "ambiguous", "본문이 복수 문서에 있다."),
    ReviewSpec("D04", "인건비 산정 내용 있는 사업계획서 찾아줘", "body_text", "medium", "lobodoc&&사업계획", "ambiguous", "본문+파일명 단서."),
    ReviewSpec("D05", "공공데이터 활용 기준 문서 보여줘", "body_text", "hard", "심사기준 변경 대비표", "candidate", "PDF 본문 단서."),
    ReviewSpec("D06", "환자 낙상 알림 화면 자료 찾아줘", "body_text", "hard", "와이어프레임||타깃디자인", "ambiguous", "UI 본문 단서."),
    ReviewSpec("D07", "프롬프트 엔지니어링 교육 보고서 찾아줘", "body_text", "hard", "컨설팅 결과보고서_사운드리더", "candidate", "PDF 본문 단서."),
    ReviewSpec("D08", "ERP 재고 관리 교육 자료 찾아줘", "body_text", "hard", "컨설팅 결과보고서_와이비세미콘", "candidate", "PDF 본문 단서."),
    ReviewSpec("E01", "환자 보호자와 응급 알림이 보이는 화면 찾아줘", "ai_metadata", "hard", "04-patient-detail.png", "candidate", "이미지 AI 설명."),
    ReviewSpec("E02", "실시간 환자 모니터링 이미지 보여줘", "ai_metadata", "medium", "04-patient-detail.png", "candidate", "AI 태그/설명."),
    ReviewSpec("E03", "복지 이용률을 높이는 행동 개입 사업계획 찾아줘", "ai_metadata", "hard", "(제출서류②) 사업계획서 1부.hwp", "ambiguous", "AI 요약 단서."),
    ReviewSpec("E04", "AI 복지 안내 플랫폼 참가 신청서 찾아줘", "ai_metadata", "hard", "(제출서류①) 참가신청서 1부.hwp", "ambiguous", "HWP AI 설명."),
    ReviewSpec("E05", "ERP 자재 입출고 훈련 보고서 찾아줘", "ai_metadata", "hard", "sojt결과보고서_와이비세미콘.hwp", "candidate", "AI metadata 단서."),
    ReviewSpec("E06", "개인정보 보유 기간이 적힌 동의서 찾아줘", "ai_metadata", "hard", "(제출서류③) 개인정보 수집·이용 동의서 1부.hwp", "ambiguous", "HWP AI 설명."),
    ReviewSpec("E07", "사회보장 서비스 발굴 참가 양식 찾아줘", "ai_metadata", "hard", "(제출서류①) 참가신청서 1부.hwp", "ambiguous", "AI 태그/설명."),
    ReviewSpec("E08", "재고관리 실무 훈련 결과 문서 보여줘", "ai_metadata", "hard", "sojt결과보고서_와이비세미콘.hwp", "candidate", "AI metadata 단서."),
)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _expected(rows: list[dict], selector: str) -> list[str]:
    groups = [group.split("&&") for group in selector.split("||")]
    return sorted({_norm(row["file_path"]) for row in rows
                   if any(all(term.casefold() in row["file_path"].casefold()
                              for term in group) for group in groups)})


def _review_path(path: str, downloads_root: Path) -> str:
    """Keep review context while masking common personal path fragments."""
    relative = os.path.relpath(path, downloads_root)
    relative = re.sub(r"(?<=과제_)[가-힣]{2,4}", "[사용자]", relative)
    relative = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", relative)
    relative = re.sub(r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)",
                      "[phone]", relative)
    return relative


def build_review(db_path: str, output: Path, downloads_root: Path) -> None:
    engine = SearchEngine(db_path)
    rows = engine._load_candidates()  # benchmark-only, read-only snapshot access
    engine.search_files_smart(["warmup"])
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ("id", "query", "category", "difficulty", "expected_candidates",
              "current_top5", "expected_rank", "latency_ms", "ground_truth_status",
              "verified_expected_paths", "review_note")
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for spec in SPECS:
            expected = _expected(rows, spec.selector)
            parsed = SearchView._parse_natural_query(None, spec.query)
            started = time.perf_counter()
            found, _ = engine.search_files_smart(
                parsed["query_keywords"], parsed["target_extension"]
            )
            elapsed = (time.perf_counter() - started) * 1000
            ranked = [_norm(row[2]) for row in found]
            expected_rank = next((index + 1 for index, path in enumerate(ranked)
                                  if path in set(expected)), "")
            writer.writerow({
                "id": spec.id, "query": spec.query, "category": spec.category,
                "difficulty": spec.difficulty,
                "expected_candidates": json.dumps([_review_path(path, downloads_root)
                                                   for path in expected],
                                                  ensure_ascii=False),
                "current_top5": json.dumps([_review_path(row[2], downloads_root)
                                            for row in found[:5]],
                                           ensure_ascii=False),
                "expected_rank": expected_rank, "latency_ms": f"{elapsed:.2f}",
                "ground_truth_status": spec.status, "verified_expected_paths": "",
                "review_note": spec.note,
            })


if __name__ == "__main__":
    build_review(
        str(ROOT / "file_manager.db"),
        ROOT / "benchmarks" / "search_ground_truth_review.csv",
        Path(r"C:\Users\USER1\Downloads"),
    )
