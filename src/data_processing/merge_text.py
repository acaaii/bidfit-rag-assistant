"""3단계: 원본 재파싱 결과와 CSV 기존 텍스트 비교/병합.

정책:
  - 원본 파일(files/파일명)이 존재하면 포맷에 맞는 파서로 재파싱을 시도한다.
  - 재파싱이 성공하고 빈 텍스트만 아니면 무조건 재파싱 결과를 채택한다
    (source="raw_parsed"). [2026-08-27 변경] 예전엔 "재파싱 텍스트가 CSV
    텍스트보다 짧고 표도 없으면 CSV 유지"라는 길이 비교 휴리스틱이 있었는데,
    텍스트 길이는 정보량의 부정확한 프록시라서(공백/줄바꿈 처리 방식 차이로
    재파싱 쪽이 우연히 더 짧아 보일 수 있음) CSV에는 없고 재파싱에만 있는
    정보를 이 규칙 때문에 놓칠 위험이 있었다. 원본 파일을 직접 재파싱한
    결과가 CSV의 1차 추출본보다 근본적으로 더 신뢰할 수 있는 소스라고 보고,
    길이와 무관하게 항상 우선하기로 했다.
  - 원본 파일이 없거나 재파싱이 실패했거나(에러) 재파싱 결과가 빈 텍스트인
    경우에만 CSV의 기존 텍스트로 폴백한다(source="csv_fallback").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from ..config import MERGED_DOCS_PATH, RAW_FILES_DIR
from .load_metadata import _load_duplicate_exclusions, resolve_budget_from_text, resolve_deadline_from_text
from .parsers import classify, hwp_parser, pdf_parser

MergeSource = Literal["raw_parsed", "csv_fallback"]


@dataclass
class MergedDoc:
    doc_id: str
    text: str
    n_tables: int
    source: MergeSource
    doc_type: str
    doc_type_confidence: str
    doc_type_reason: str
    parse_note: Optional[str] = None


# --- 셀 접근 duck-typing 헬퍼 -------------------------------------------
# hwp 문서는 hwp_parser.TableCell 객체(텍스트+colspan+rowspan+배경색)로
# 셀을 표현하지만, pdf_parser는 여전히 plain str로 셀을 표현한다(pdfplumber
# extract_tables() 결과 그대로). 이 모듈이 두 소스를 모두 다뤄야 하므로,
# 아래 헬퍼로 어느 쪽이 와도 동작하게 한다 - str에는 colspan/bg_color 개념이
# 없으니 각각 1/None으로 취급한다.
def _cell_text(cell) -> str:
    return cell.text if hasattr(cell, "text") else (cell or "")


def _cell_colspan(cell) -> int:
    return getattr(cell, "colspan", 1) or 1


def _cell_bg(cell):
    return getattr(cell, "bg_color", None)


# "M"/"M+1"/"M+2".../ 처럼 기준월(M) 기준 상대 기간을 나타내는 헤더 셀 패턴.
# "추진일정" 표 캡처(2026-08-28, 국방과학연구소 문서)에서 실제로 이 표기를
# 씀을 확인했다.
_PERIOD_HEADER_RE = re.compile(r"^M(\+\d+)?$")


def _detect_period_header_row(table: list) -> Optional[tuple]:
    """표의 첫 두 행 안에서 "M"/"M+1"/... 패턴의 기간 헤더 행을 찾는다.

    찾으면 (헤더 행 인덱스, [(그리드 시작열, 그리드 끝열, 기간라벨), ...])를
    반환한다 - colspan을 누적해서 각 기간 라벨이 차지하는 그리드 열 범위를
    계산한다. 기간 라벨이 2개 미만이면(우연히 "M" 하나만 있는 경우까지
    기간표로 오판하지 않으려고) None을 반환해서 호출부가 예전처럼 단순
    텍스트 flatten으로 처리하게 한다."""
    for row_i, row in enumerate(table[:2]):
        offset = 0
        periods = []
        for cell in row:
            text = _cell_text(cell).strip()
            colspan = _cell_colspan(cell)
            if _PERIOD_HEADER_RE.match(text):
                periods.append((offset, offset + colspan, text))
            offset += colspan
        if len(periods) >= 2:
            return row_i, periods
    return None


def _is_shaded(cell, baseline_colors: set) -> bool:
    """이 셀이 "활성"으로 봐야 하는지 판단한다. 텍스트가 있으면(▲ 등) 그
    자체로 의미 있는 표시라 무조건 활성으로 본다. 텍스트가 없으면 배경색이
    이 표의 "기본(비활성)" 색과 다른지로 판단한다."""
    if _cell_text(cell).strip():
        return True
    return _cell_bg(cell) not in baseline_colors


def _flatten_gantt_table(table: list) -> Optional[str]:
    """"M"/"M+1"/... 기간 헤더가 있는 표(추진일정 같은 Gantt형 표)를
    "작업명: 활성 기간" 형태의 읽을 수 있는 텍스트로 풀어쓴다.

    [2026-08-28 발견] 국방과학연구소_기록관리시스템 통합 활용 및 보안 환경
    구축.hwp의 "6. 추진일정" 표를 scripts/diagnose_gantt_table_shading_v2.py
    로 실제 셀 속성까지 덤프해서 확인한 내용: 활성 기간 칸은 텍스트가 비어
    있고(문단기호만 있음) 대신 배경색(그 문서 기준 class="borderfill-93/94/82"
    -> #a3d7dd, 파란 계열)으로만 표시된다. 비활성 칸은 배경색 지정이 아예
    없는 class(borderfill-6/12/13)를 쓴다. 그래서 텍스트만 보는 예전 방식
    (_flatten_one_table)으로는 활성 기간 칸이 전부 빈 문자열이 되고, 빈
    문자열만 있는 행은 통째로 스킵돼서 표 내용이 사라졌다.

    특정 색상값(#a3d7dd 등)을 하드코딩하지 않고 일반화한다: 표 안 기간
    그리드 셀들의 배경색 중 가장 흔한 값을 "기본(비활성)"으로 보고, 그와
    다른 배경색(또는 ▲ 같은 명시적 텍스트)을 "활성"으로 판단한다 - Gantt
    표는 보통 활성 구간이 소수이므로 다수결이 합리적인 기준이 된다. 이렇게
    하면 다른 문서가 다른 색상을 쓰거나 아예 다른 인코딩(예: 진하게 표시)을
    써도 최소한 "무엇이 소수파인지"는 잡아낼 여지가 있다(완벽한 일반화는
    아니고, 실제 다른 표들로 더 검증이 필요함 - 아래 함수 docstring 끝 참고).

    기간 헤더를 못 찾으면(즉 이 표가 Gantt형이 아니면) None을 반환해서
    호출부(_flatten_one_table)가 예전 방식으로 폴백하게 한다."""
    header = _detect_period_header_row(table)
    if header is None:
        return None
    header_row_i, periods = header
    total_grid_width = periods[-1][1]
    grid_rows = table[header_row_i + 1:]

    # 1) 각 행을 "작업 행"(기간 그리드 폭과 얼추 일치)과 "구분 행"(그보다
    #    훨씬 좁은 폭 - 표 전체를 덮는 섹션 제목, colspan이 label 하나로
    #    grid 폭 전체를 차지)으로 나눈다.
    parsed_rows = []  # (label, period_cells | None)
    for row in grid_rows:
        if not row:
            continue
        label_cell = row[0]
        rest = row[1:]
        rest_width = sum(_cell_colspan(c) for c in rest)
        if rest and rest_width >= total_grid_width - 1:
            parsed_rows.append((_cell_text(label_cell).strip(), rest))
        else:
            parsed_rows.append((_cell_text(label_cell).strip(), None))

    # 2) 작업 행들의 기간 그리드 셀 배경색 분포로 "기본(비활성)" 색을
    #    다수결로 정한다.
    color_counts: dict = {}
    for _, period_cells in parsed_rows:
        if period_cells is None:
            continue
        for cell in period_cells:
            bg = _cell_bg(cell)
            color_counts[bg] = color_counts.get(bg, 0) + 1
    baseline_colors = set()
    if color_counts:
        max_count = max(color_counts.values())
        baseline_colors = {c for c, n in color_counts.items() if n == max_count}
        # 다수결이 동점이고 그 안에 "배경색 지정 없음"(None)이 끼어있으면
        # None을 우선한다 - 아무 색도 안 칠해진 쪽이 "기본/비활성"에 더
        # 자연스럽게 부합하는 경우가 많다(표본이 적은 표에서 특정 색상 하나와
        # None이 우연히 동률이 되는 경우의 안전한 타이브레이커).
        if None in color_counts and color_counts[None] == max_count:
            baseline_colors = {None}

    # 3) 각 작업 행에 대해 활성 기간을 판별해서 텍스트로 만든다.
    lines = []
    for label, period_cells in parsed_rows:
        if not label:
            continue
        if period_cells is None:
            lines.append(f"[{label}]")
            continue
        offset = 0
        active_periods: list = []
        # [2026-08-28 추가] ▲ 같은 마일스톤 표시 문자가 있던 칸인지를
        # has_marker_text로 따로 기억해뒀다가, 결과에 "(시점)"/"(기간)"이라는
        # 한국어 단어로 풀어서 표시한다.
        #
        # [2026-08-28 재수정, 한빈 지적] 처음엔 원본 기호(▲)를 그대로
        # "▲ M, M+3" 식으로 붙였는데, 이러면 그 표를 실제로 본 적 없는
        # 임베딩/답변 모델 입장에선 "▲"가 무슨 뜻인지 학습된 관례가 없어서
        # 그냥 의미 없는 기호로만 보일 수 있다("이게 마일스톤이라고 모델이
        # 알아들을까?"). 반면 "시점"/"기간" 같은 한국어 단어는 별도 설명 없이
        # 바로 이해되므로, 기호를 그대로 남기는 대신 그 기호가 뜻하는 바
        # (표에서 텍스트로 명시적 표시가 된 칸 = 그 시점에 일어나는 이벤트,
        # 배경색만으로 표시된 칸 = 그 기간 동안 진행되는 작업)를 말로 풀어쓴다.
        # 한 행 안에서 텍스트 표시와 배경색 표시가 섞이는 경우는 실제
        # 문서에서 못 봤지만(마일스톤 행은 전부 ▲만, 작업 행은 전부 배경색만
        # 썼음), 혹시 섞이면 "시점" 쪽을 우선한다(더 구체적인 정보이므로).
        has_marker_text = False
        for cell in period_cells:
            colspan = _cell_colspan(cell)
            cell_text = _cell_text(cell).strip()
            if cell_text:
                has_marker_text = True
            if _is_shaded(cell, baseline_colors):
                for p_start, p_end, p_label in periods:
                    if offset < p_end and offset + colspan > p_start and p_label not in active_periods:
                        active_periods.append(p_label)
            offset += colspan
        if active_periods:
            kind = "시점" if has_marker_text else "기간"
            lines.append(f"{label} ({kind}): {', '.join(active_periods)}")
        else:
            lines.append(f"{label}: (표시된 기간 없음)")

    if not lines:
        return None
    return "[표: 기간표(추진일정 등)로 추정]\n" + "\n".join(lines)


def _flatten_one_table(table: list) -> str:
    """표 하나(행의 리스트)를 텍스트 블록 하나로 풀어쓴다.

    [2026-08-27 발견] 표 추출을 재활성화(hwp_parser 참고)하고 나서도, 이
    함수가 없으면 표 안의 실제 내용은 n_tables 카운트에만 반영될 뿐 어디에도
    쓰이지 않는 죽은 데이터였다 - 예산/마감일 후보 탐지(_extract_budget_candidate
    등)와 chunking이 전부 `text` 컬럼만 보는데, parsed.tables는 그 `text`에
    한 번도 합쳐진 적이 없었기 때문이다. 실제로 경희대학교 문서에서 이 문제가
    드러났다: 예산 정보(총 사업예산 400,000,000원)가 표 셀 안에 있는데,
    hwp5txt가 뽑는 본문 텍스트에는 그 표 내용이 빠져 있어서 재파싱 후 예산
    후보가 오히려 "candidate" -> "no_info"로 퇴보했다. 이 함수로 표 내용을
    풀어쓰면 예산/마감일 후보 탐지와 임베딩/청킹 양쪽 다 표 안의 내용을 볼 수
    있게 된다.

    [2026-08-28 변경] 먼저 _flatten_gantt_table()로 "M"/"M+1"/... 기간 헤더가
    있는 Gantt형 표인지 확인하고, 맞으면 그 결과(활성 기간을 텍스트로 풀어쓴
    것)를 쓴다 - 이런 표는 활성 기간이 셀 배경색으로만 표시돼서 아래의 단순
    "칸 텍스트를 ' | '로 이어붙이는" 방식으로는 애초에 복구가 안 된다(추진
    일정표의 문단기호(↵)만 있는 빈 칸 등 - 셀이 전부 빈 문자열이면 그 행이
    통째로 스킵됨). Gantt형이 아니면(기간 헤더를 못 찾으면) 아래 예전 방식
    그대로 처리한다.

    빈 표(또는 모든 행이 빈 문자열)면 빈 문자열을 반환한다."""
    gantt_text = _flatten_gantt_table(table)
    if gantt_text is not None:
        return gantt_text

    lines = []
    for row in table:
        if not row:
            continue
        line = " | ".join(_cell_text(cell) for cell in row if _cell_text(cell))
        if line:
            lines.append(line)
    if not lines:
        return ""
    return "[표]\n" + "\n".join(lines)


def _normalize_for_dup_check(s: str) -> str:
    """공백/줄바꿈을 다 지워서 비교용으로 정규화한다. PyMuPDF/hwp5txt의
    줄바꿈 방식과 pdfplumber 표 셀 텍스트의 줄바꿈 방식이 달라서, 공백을
    남겨두면 실제로는 같은 내용인데 다르다고 오판할 수 있다."""
    return re.sub(r"\s+", "", s or "")


# "완전중복"으로 보고 걸러낼 문턱값. scripts/diagnose_pdf_table_duplication.py로
# 실제 PDF 문서 2건(기초과학연구원 49p/표66개, 서울특별시 74p/표167개 - 총
# 233개 표)을 검사한 결과, 표 셀 텍스트가 90% 이상 이미 본문에 그대로
# 들어있는("완전중복") 표가 208개(89%)였고 그 중 "표에만 있는 고유 정보"는
# 0개였다. 반면 50~88%대 "부분중복" 표(대부분 페이지 상단 반복 서식 헤더의
# 사소한 포맷 차이로 문턱을 못 넘긴 경우로 보이지만, 표 일부 셀만 진짜
# 고유했을 가능성도 배제 못 함)까지 걸러내면 실제 고유 정보를 날릴 위험이
# 있어, 확실히 중복인 구간만 보수적으로 잡는다.
_DUP_DROP_THRESHOLD = 0.9


def _table_duplicate_ratio(table: list, body_norm: str) -> float:
    """표의 (비어있지 않은) 셀 텍스트 중 이미 정규화된 본문 텍스트에 그대로
    들어있는 비율(0~1)을 계산한다.

    [2026-08-28 발견] PDF는 HWP와 근본적으로 다르다: hwp5txt는 표 내용을
    본문에서 아예 떼어내고 "<표>" 자리표시자로 대체하지만(그래서
    _insert_tables_inline이 잘 통함), PyMuPDF의 page.get_text()는 표
    영역이든 아니든 페이지 위 모든 문자를 그냥 다 뽑아버린다 - PDF 자체가
    "표"라는 구조 개념이 없고 그저 위치가 찍힌 문자+선일 뿐이라서다. 그
    결과 pdfplumber가 별도로 뽑아낸 "표"는 이미 본문 텍스트 안에 그
    내용이 다 들어있는 경우가 압도적으로 많다(위 _DUP_DROP_THRESHOLD
    문서 참고, 실측 233개 표 중 완전중복 89%/고유 0%). 이런 표까지 폴백
    경로에서 문서 끝에 또 붙이면 같은 내용이 두 번 들어가 청킹/임베딩에
    순수 노이즈만 늘리는 꼴이라 걸러낸다.

    HWP는 정상적으로는 이 비율이 낮게 나온다(표 내용이 hwp5txt 본문에
    없으므로) - 그래서 이 필터를 HWP 폴백 경로에도 똑같이 적용해도
    무해하다(안전망일 뿐, 실제로 걸릴 일이 거의 없음)."""
    flat_cells = [_cell_text(c) for row in table for c in row if _cell_text(c).strip()]
    if not flat_cells:
        return 0.0
    found = sum(1 for c in flat_cells if _normalize_for_dup_check(c) in body_norm)
    return found / len(flat_cells)


def _filter_duplicate_tables(tables: list, body_text: str) -> tuple[list, int]:
    """body_text와 "완전중복"(_DUP_DROP_THRESHOLD 이상)인 표를 걸러낸다.

    (걸러내고 남은 표 리스트, 걸러낸 개수) 튜플을 반환한다. body_text가
    비어 있으면(호출부가 안 넘겼거나 빈 문자열) 아무것도 걸러내지 않고
    원본 tables를 그대로 반환한다 - 하위호환/안전 기본값."""
    if not body_text or not tables:
        return tables, 0
    body_norm = _normalize_for_dup_check(body_text)
    kept, n_dropped = [], 0
    for t in tables:
        if _table_duplicate_ratio(t, body_norm) >= _DUP_DROP_THRESHOLD:
            n_dropped += 1
        else:
            kept.append(t)
    return kept, n_dropped


def _flatten_tables(tables: list) -> str:
    """표 여러 개를 순서대로 풀어써서 하나의 텍스트로 이어붙인다(문서 끝에
    몰아 붙이는 폴백 경로 전용 - 아래 _insert_tables_inline 문서 참고).

    [2026-08-28 변경] 원래 merge_one()이 항상 이 함수로 모든 표 내용을
    본문 맨 끝에 붙였는데, 그러면 표가 원래 있던 자리(예: "6. 추진일정"
    제목 바로 다음)의 문맥이 사라져서, 표만 담긴 chunk가 어느 섹션 표인지
    알 수 없게 되는 문제가 있었다(한빈 지적). 이제 merge_one()은 우선
    _insert_tables_inline()으로 "<표>" 자리표시자 위치에 표 내용을 직접
    끼워넣는 걸 시도하고, 이 함수(문서 끝 일괄 첨부)는 자리표시자 개수와
    표 개수가 안 맞아 위치를 신뢰할 수 없을 때만 폴백으로 쓴다.

    [2026-08-28 추가] 이 함수 자체는 필터링을 하지 않는다 - 호출부
    (merge_one)가 먼저 _filter_duplicate_tables()로 본문과 완전중복인
    표를 걸러낸 다음, 남은 표만 이 함수에 넘기는 구조다(책임 분리: 이
    함수는 "표를 텍스트로 어떻게 풀어쓸지"만 담당)."""
    if not tables:
        return ""
    blocks = [b for b in (_flatten_one_table(t) for t in tables) if b]
    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks)


_TABLE_PLACEHOLDER_LINE_RE = re.compile(r"^<표>$")


def _strip_table_placeholders(text: str) -> str:
    """"<표>" 자리표시자 줄을 지운다. _insert_tables_inline()이 실패해서
    _flatten_tables() 폴백(문서 끝 일괄 첨부)을 쓸 때, 원래 자리에 남은
    자리표시자까지 본문에 남기면 의미 없는 "<표>" 줄만 늘어나므로 이 때만
    지운다(hwp_parser._strip_hwp5txt_placeholders가 예전에 항상 하던 일과
    같은데, 이제 "<표>"는 기본으로 안 지우므로 폴백 경로에서만 명시적으로
    호출한다)."""
    if not text:
        return text
    lines = text.split("\n")
    kept = [ln for ln in lines if not _TABLE_PLACEHOLDER_LINE_RE.match(ln.strip())]
    cleaned = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def _insert_tables_inline(raw_text: str, tables: list) -> tuple[str, bool]:
    """raw_text 안의 "<표>" 자리표시자 줄 자리에 각 표 내용을 순서대로
    끼워넣는다. 반환값 두 번째 요소가 True면 성공(교체된 raw_text 반환),
    False면 실패(원본 raw_text를 그대로 반환 - 호출부가 _flatten_tables()
    폴백으로 이어가야 함을 알리는 신호).

    [2026-08-28 신설] hwp5txt(본문 텍스트, "<표>" 자리표시자 포함)와
    hwp5html(표 구조, tables 리스트) 둘 다 문서를 처음부터 순서대로 훑는
    별도 변환이라, "<표>" 줄이 나오는 순서와 tables 리스트의 순서가
    대응한다고 기대할 수 있다 - 다만 이건 pyhwp 내부 동작에 대한 관찰/추정
    이지 100% 문서화된 계약은 아니다. 그래서 자리표시자 개수와 표 개수가
    정확히 같을 때만 끼워넣기를 시도하고, 하나라도 다르면(표 추출이 일부만
    성공했거나, 자리표시자가 없는 특이한 표 구조 등) 절대 억지로 순서를
    맞추지 않고 안전하게 실패 처리한다 - 잘못 끼워넣으면 엉뚱한 표 내용이
    엉뚱한 제목 밑에 붙는, 지금 문제(문맥 없음)보다 더 나쁜 결과가 나올 수
    있어서다."""
    if not tables:
        return raw_text, False
    lines = raw_text.split("\n")
    placeholder_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "<표>"]
    if len(placeholder_idxs) != len(tables):
        return raw_text, False
    for idx, table in zip(placeholder_idxs, tables):
        lines[idx] = _flatten_one_table(table)  # 빈 문자열이면 그 줄이 빈 줄로 남고 아래서 정리됨
    merged = "\n".join(lines)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    return merged, True


def _try_raw_parse(file_name: str, file_format: str):
    """원본 파일이 있으면 파싱 시도. 반환: (ParseResult | None, note)."""
    path = RAW_FILES_DIR / file_name
    if not path.exists():
        return None, "원본 파일 없음 -> CSV 텍스트로 폴백"

    fmt = file_format.strip().lower()
    if fmt == "hwp":
        # extract_tables_flag는 hwp_parser.py 기본값(True)을 그대로 따른다 - 2026-08
        # 진단으로 표 추출 자체는 정상 동작하고, 옛 20초 타임아웃이 원인이었음이
        # 확인돼서 표 추출을 다시 켰다(타임아웃도 90초로 상향). 문서당 최대 90초씩
        # 걸릴 수 있어 96건 전체 재실행은 최악의 경우 2시간 넘게 걸릴 수 있지만,
        # 결과가 output/merged_docs.pkl에 캐시되므로 한 번만 감수하면 된다.
        result = hwp_parser.parse_hwp(path, extract_tables_flag=True)
    elif fmt == "pdf":
        result = pdf_parser.parse_pdf(path)
    else:
        return None, f"지원하지 않는 파일형식: {file_format}"

    if not result.ok:
        return None, f"재파싱 실패({result.error}) -> CSV 텍스트로 폴백"
    return result, None


def merge_one(row: pd.Series) -> MergedDoc:
    doc_id = row["doc_id"]
    csv_text = row["텍스트"] or ""

    parsed, note = _try_raw_parse(row["파일명"], row["파일형식"])

    if parsed is not None:
        raw_text = parsed.text.strip()
        # 길이 비교 없이, 재파싱이 성공하고 빈 텍스트만 아니면 항상 채택한다
        # (모듈 docstring의 2026-08-27 변경 참고).
        if raw_text:
            n_pages = getattr(parsed, "n_pages", None)
            n_scanned = getattr(parsed, "n_scanned_pages", None)
            used_ocr = getattr(parsed, "used_ocr", False)
            # 문서유형 분류는 본문(paragraph) 텍스트 길이만 기준으로 한다 - 표
            # 내용을 합친 길이로 재면 "스캔본이라 텍스트가 거의 없음" 같은 신호가
            # 표 유무에 따라 왜곡될 수 있어서다.
            cls = classify.classify_with_parse_result(
                text_len=len(raw_text), n_tables=parsed.n_tables,
                n_pages=n_pages, n_scanned_pages=n_scanned, used_ocr=used_ocr,
            )
            accept_note = "원본 재파싱 채택"
            text_note = getattr(parsed, "text_note", None)
            if text_note:
                # hwp_parser.py의 "우회 추출"(2026-08-27 추가) 경로가 실제로 쓰였을
                # 때만 채워진다 - 정상 경로(hwp5txt 서브프로세스 성공)면 항상 None.
                # 이 문서가 pyhwp 내부 XML 손상 문제로 정상 경로가 실패해서 우회
                # 경로로 재추출됐다는 사실을 나중에 이 노트로 추적할 수 있다.
                accept_note += f" | 본문: {text_note}"
            mupdf_warnings = getattr(parsed, "mupdf_warnings", "")
            if mupdf_warnings:
                # PDF가 살짝 손상/비표준이어도 MuPDF가 복구해서 텍스트는 뽑아준
                # 경우 - 콘솔 스팸은 pdf_parser.py에서 껐지만, 이 문서는 표본으로
                # 눈으로 한 번 확인해볼 가치가 있어 parse_note에 남겨둔다.
                accept_note += f" (MuPDF 경고 있었음: {mupdf_warnings[:200]})"
            table_note = getattr(parsed, "table_note", None)
            if table_note:
                # hwp 문서의 표 추출 시도 결과(성공/실패/비활성화). n_tables=0이
                # "표가 없다"인지 "추출을 아예 안 했다/실패했다"인지 여기서 구분된다.
                accept_note += f" | 표: {table_note}"
            # 표 내용을 본문에 합친다. 이걸 안 하면 표 추출(parsed.tables)이
            # 성공해도 예산/마감일 후보 탐지와 임베딩/청킹 어디에도 표 안
            # 내용이 반영되지 않는다.
            #
            # [2026-08-28 변경] 예전엔 무조건 문서 끝에 표를 몰아 붙였는데,
            # 그러면 "6. 추진일정" 같은 제목/문맥이 사라지는 문제가 있었다
            # (한빈 지적). 이제 먼저 _insert_tables_inline()으로 "<표>"
            # 자리표시자 위치(=표가 원래 있던 자리)에 표 내용을 직접
            # 끼워넣는 걸 시도하고, 자리표시자 개수와 표 개수가 안 맞아
            # 위치를 신뢰할 수 없을 때만 예전 방식(_flatten_tables로 문서
            # 끝 일괄 첨부, 자리표시자는 지움)으로 안전하게 폴백한다.
            tables = getattr(parsed, "tables", None) or []
            inline_text, inline_ok = _insert_tables_inline(raw_text, tables)
            if inline_ok:
                full_text = inline_text
                if tables:
                    accept_note += " | 표 내용을 원래 위치에 끼워넣음(inline, 문맥 유지)"
            else:
                # [2026-08-28 추가] 문서 끝에 붙이기 전에 본문과 완전중복인
                # 표를 걸러낸다 - _filter_duplicate_tables()/_table_duplicate_ratio()
                # 문서 참고. PDF는 표가 있으면 자리표시자가 항상 0개라
                # 무조건 이 폴백 경로로 오는데(pdf_parser는 "<표>" 자리표시자
                # 개념 자체가 없음), 실측 결과(scripts/diagnose_pdf_table_duplication.py,
                # 233개 표 검사) 그런 표의 89%가 본문과 완전중복이라 걸러내지
                # 않으면 순수 중복 삽입이 된다.
                kept_tables, n_dropped_dup = _filter_duplicate_tables(tables, raw_text)
                table_text = _flatten_tables(kept_tables)
                full_text = _strip_table_placeholders(raw_text) + table_text
                if tables:
                    n_placeholders = sum(1 for ln in raw_text.split("\n") if ln.strip() == "<표>")
                    dup_note = f", 본문과 완전중복인 표 {n_dropped_dup}/{len(tables)}개는 제외" if n_dropped_dup else ""
                    accept_note += (
                        f" | 표 자리표시자 개수({n_placeholders})와 추출된 표 개수({len(tables)}) 불일치"
                        f" -> inline 삽입 포기, 표 내용을 문서 끝에 일괄 첨부(폴백{dup_note})"
                    )
            return MergedDoc(
                doc_id=doc_id, text=full_text, n_tables=parsed.n_tables,
                source="raw_parsed", doc_type=cls.doc_type,
                doc_type_confidence=cls.confidence, doc_type_reason=cls.reason,
                parse_note=accept_note,
            )
        note = "재파싱 결과가 빈 텍스트라 CSV로 폴백"

    cls = classify.classify_by_text_length(len(csv_text))
    return MergedDoc(
        doc_id=doc_id, text=csv_text, n_tables=0, source="csv_fallback",
        doc_type=cls.doc_type, doc_type_confidence=cls.confidence,
        doc_type_reason=cls.reason, parse_note=note,
    )


def merge_all(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    total = len(df)
    merged = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        if verbose:
            # 원본 hwp/pdf가 있으면 문서 1건당 subprocess(hwp5txt/hwp5html) 호출이
            # 여러 번 일어나 수 초씩 걸릴 수 있다. 진행상황이 안 보이면 멈춘 것처럼
            # 보이므로 몇 번째 문서를 처리 중인지 매번 출력한다.
            print(f"  [{i}/{total}] {row['파일명']} 처리 중...", flush=True)
        merged.append(merge_one(row))
    merged_df = pd.DataFrame([m.__dict__ for m in merged])
    result = df.merge(merged_df, on="doc_id", suffixes=("", "_merged"))
    # 예산/마감일 결측 재평가는 CSV 1차 추출 텍스트가 아니라 여기서 만든 최종
    # 본문(text, 원본 재파싱 성공 시 그 결과)을 기준으로 해야 한다 - load_metadata.py
    # 상단 docstring 1/2번 참고. CSV 텍스트만 보고 "본문에도 없음"이라고 성급하게
    # 판단하면, 원본을 재파싱했을 때만 드러나는 정보를 놓치게 된다.
    result = resolve_budget_from_text(result, text_col="text")
    result = resolve_deadline_from_text(result, text_col="text")
    return result


def save_merged(df: pd.DataFrame, path: Path = MERGED_DOCS_PATH) -> None:
    """merge_all() 결과를 output/merged_docs.pkl에 저장 (다음 단계에서 재사용)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    print(f"[merge_text] 저장됨: {path}")


def load_merged(path: Path = MERGED_DOCS_PATH) -> Optional[pd.DataFrame]:
    """캐시된 결과가 있으면 불러오고, 없으면 None.

    캐시를 불러올 때도 예산/마감일 결측 재평가(resolve_budget_from_text/
    resolve_deadline_from_text)를 다시 적용한다 - 정규식 재검사만 하는
    가벼운 연산이라, 예전에 만들어둔 output/merged_docs.pkl(예: 원본 100건
    재파싱에 몇 시간 걸린 결과)을 다시 파싱하지 않고도 최신 판단 로직을
    그대로 적용받을 수 있다.

    [2026-08-27 추가] hwp5txt가 본문에 박아넣는 "<그림>" 자리표시자
    (hwp_parser._strip_hwp5txt_placeholders 참고, 10절 문서화)도 같은
    이유로 여기서 같이 지운다 - 이건 hwp5txt/hwp5html을 다시 실행해야만
    나오는 정보가 아니라 이미 뽑아둔 text 컬럼에서 그 패턴만 지우면 되는
    순수 텍스트 후처리라서, 몇 시간짜리 전체 재파싱
    (scripts/step2_merge_text.py를 처음부터 다시 돌리는 것) 없이도
    캐시를 불러올 때마다 즉시 적용된다.

    [2026-08-28 변경] 예전엔 "<표>"도 여기서 같이 지웠는데, 이제
    merge_one()이 "<표>" 자리표시자를 표 내용을 원래 자리에 끼워넣는
    이정표로 쓰기 때문에(hwp_parser._strip_hwp5txt_placeholders 상단
    주석, 아래 _insert_tables_inline 참고), 새로 재파싱한 문서의 text에는
    "<표>"가 남아있지 않아야 정상이다(성공했으면 실제 표 내용으로,
    실패해도 폴백 경로에서 이미 지워짐). 그래서 hwp_parser._strip_hwp5txt_placeholders
    가 더 이상 "<표>"를 지우지 않게 바뀌었어도, 이 줄을 그대로 남겨둬도
    무해하다(지울 대상이 이미 없는 경우가 정상) - 혹시 모를 예외적인
    잔여 자리표시자에 대한 안전망으로만 유지한다. `_flatten_tables()`/
    `_insert_tables_inline()`이 만드는 실제 표 내용 블록은
    "[표]"(대괄호)를 쓰고 이 자리표시자는 "<표>"(꺾쇠괄호)라 서로 다른
    표기라서, 이미 병합된 최종 text에 적용해도 실제 표 내용이 잘못 지워질
    위험은 없다.

    [2026-08-28 추가] duplicate_exclusions.csv에 적힌 문서(같은 공고가 중복
    수집된 것으로 확인된 건, load_metadata.check_duplicate_titles 문서화
    참고)도 캐시에서 걸러낸다 - 이미 몇 시간 걸려 만들어둔 output/merged_docs.pkl
    을 다시 재파싱하지 않고, 캐시를 불러오는 시점에 딱 그 문서 행만 제거하는
    식으로 처리한다."""
    if not path.exists():
        return None
    df = pd.read_pickle(path)
    df["text"] = df["text"].apply(hwp_parser._strip_hwp5txt_placeholders)
    df = resolve_budget_from_text(df, text_col="text")
    df = resolve_deadline_from_text(df, text_col="text")
    exclusions = _load_duplicate_exclusions()
    if exclusions is not None:
        to_drop = set(exclusions["doc_id"])
        mask = df["doc_id"].isin(to_drop)
        if mask.any():
            for doc_id in df.loc[mask, "doc_id"]:
                print(f"[merge_text] 캐시에서 중복 문서 제외: {doc_id!r}")
            df = df.loc[~mask].reset_index(drop=True)
    return df


if __name__ == "__main__":
    from .load_metadata import load_clean_metadata

    df = load_clean_metadata()
    result = merge_all(df)
    print(result["source"].value_counts())
    print(result["doc_type"].value_counts())
