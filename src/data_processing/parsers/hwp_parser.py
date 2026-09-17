"""hwp(HWPv5) 파서.

내부적으로 pyhwp 패키지가 제공하는 CLI(hwp5txt, hwp5html)를 사용한다.
- hwp5txt: 본문 텍스트 추출 (빠름, 기본값으로 항상 실행)
- hwp5html: 표 구조를 보존한 HTML 변환 -> BeautifulSoup으로 표를 별도 추출

  [2026-08 정정] 예전엔 "100건 처리에 약 3시간, 표는 0건 추출"이라는 실측
  결과 때문에 기본값을 꺼뒀었다(extract_tables_flag=False). 그런데
  diagnose_hwp_table.py로 실제 hwp 파일(한국생산기술연구원, 1.3MB) 하나를
  타임아웃 300초로 열어서 확인해보니: hwp5html 변환 자체는 44.2초 걸렸고,
  <table> 태그 118개 + 실제 행(row)까지 뽑힌 표 118개 전부 정상 추출됨.
  즉 BeautifulSoup 파싱 로직이나 hwp5html 자체엔 버그가 없었고, 진짜
  원인은 그 당시 걸려있던 timeout=20초였다 - 이 문서 하나만 해도 44초가
  필요해서 20초 제한에선 무조건 타임아웃으로 실패 처리됐던 것("표가 없다"가
  아니라 "확인하기 전에 시간이 끝났다"). 그래서 표 추출을 다시 켜고
  (extract_tables_flag 기본값 True), 타임아웃도 20초 -> 90초로 올렸다.
  100건 전체 기준 최악의 경우(전부 타임아웃) 약 96 x 90초 ≈ 2.4시간,
  실측 페이스(44초/건)라면 약 70분 정도로 예상 - 어차피 merge_text.py가
  결과를 output/merged_docs.pkl에 캐시해두므로 한 번만 감수하면 된다.

  [2026-08-28 추가, 한빈 발견] 98건 전체 재파싱을 연달아 두 번 돌렸더니
  "표가 1개 이상 추출된 문서 수"가 30건 -> 62건으로 뛰는 등, 실행할 때마다
  결과가 달라지는 비결정성이 발견됐다. `scripts/diagnose_table_extraction_stability.py`
  로 parse_note를 훑어보니 62건짜리 실행 자체에서도 35건(전체의 36%)이
  "표 추출 실패: hwp5html 타임아웃"이었다 - 즉 90초도 상당수 문서에 부족하고,
  그 90초 문턱 근처에서 컴퓨터가 그 순간 얼마나 바빴는지에 따라 성공/실패가
  갈리는 것으로 보인다(merge_all()이 문서를 순차 처리하므로 병렬 경합은
  아니지만, 다른 백그라운드 프로세스나 시스템 부하가 hwp5html 서브프로세스
  속도에 영향을 줄 수 있음). 표가 실제로 있는지조차 실행 운에 따라 달라지는
  건 심각한 데이터 품질 문제라, 타임아웃을 90초 -> 240초로 다시 올렸다 -
  100건 전체 기준 최악의 경우(전부 타임아웃) 약 96 x 240초 ≈ 6.4시간까지
  늘어날 수 있지만, 실제로는 대다수 문서가 몇 초~수십 초 안에 끝나고 일부
  느린 문서만 240초 여유를 다 쓰게 될 것으로 예상된다(다시 재파싱해서
  35건이 실제로 얼마나 줄어드는지, 그래도 남는 타임아웃이 있으면 그 문서가
  진짜 예외적으로 크거나 복잡한 건지 확인 필요).

HWP 3.0(구버전) 파일은 pyhwp가 지원하지 않아 실패할 수 있다. 이 경우
ParseResult.error에 사유를 남기고 상위 단계(merge_text)에서 CSV 텍스트로
폴백한다.

[2026-08-27 추가] 한국농어촌공사 AFSIS 문서처럼 극소수 hwp 파일에서
hwp5txt/hwp5html이 `lxml.etree.XMLSyntaxError: invalid character in
attribute value`로 죽는 사례를 발견했다. 아래 "우회 추출" 섹션 참고 -
원인은 우리 코드가 아니라 pyhwp 0.1b15 자체가 중간 XML을 만들 때 NUL만
지우고 다른 제어문자는 안 지우는 처리 누락이라, 그 문서에 한해서만
pyhwp를 우리 프로세스 안에서 직접 호출해 그 제어문자를 미리 걸러내는
방식으로 재추출을 시도한다.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TableCell:
    """표 셀 하나. [2026-08-28 신설] 예전엔 셀을 그냥 텍스트 문자열(str)로만
    다뤘는데, "추진일정" 같은 Gantt형 표는 활성 기간이 셀 텍스트가 아니라
    배경색으로만 표시돼서 텍스트만으로는 정보가 통째로 사라지는 문제가
    있었다(한빈 지적, 2026-08-28). 이 문제를 풀려면 셀의 배경색/colspan
    정보까지 같이 갖고 있어야 해서 이 타입을 도입했다 - merge_text.py의
    _flatten_gantt_table()이 이 정보로 "어느 칸이 칠해졌는지"를 판별한다.

    text가 비어있어도(활성 기간 표시 칸처럼) 유효한 셀이다 - 표를 구성하는
    빈 칸도 그리드 구조상 의미가 있어서 걸러내지 않는다."""
    text: str
    colspan: int = 1
    rowspan: int = 1
    bg_color: str | None = None  # 예: "#a3d7dd". 배경색 지정이 없으면 None(보통 흰색/기본값).


@dataclass
class ParseResult:
    text: str = ""
    tables: list[list[list[TableCell]]] = field(default_factory=list)  # table -> rows -> cells
    n_tables: int = 0
    source: str = "hwp5"
    error: str | None = None
    table_note: str | None = None  # 표 추출 시도 결과 메모(성공/실패 이유). n_tables=0이
    # "표가 없다"인지 "추출을 시도했다가 실패했다"인지 구분하려면 이 필드를 봐야 한다.
    text_note: str | None = None  # 본문 추출이 정상 경로(hwp5txt 서브프로세스)가 아니라
    # 아래 "우회 추출" 경로로 성공했을 때만 채워진다. 정상 경로면 항상 None.

    @property
    def ok(self) -> bool:
        return self.error is None


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    # hwp5txt/hwp5html은 UTF-8로 출력하는데, 윈도우 기본 콘솔 인코딩(cp949)으로
    # 디코딩하면 한글 구간에서 깨져서 UnicodeDecodeError가 난다. 인코딩을 명시해준다.
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace"
    )


# --- pyhwp 내부 XML 생성 손상 문서 우회 추출 ---------------------------------
# [2026-08-27 발견] 한국농어촌공사_아세안+3 식량안보정보시스템(AFSIS) 문서에서
# hwp5txt/hwp5html 둘 다 아래 traceback으로 죽는 걸 확인했다:
#   File ".../hwp5/plat/_lxml.py", line 101, in _transform
#       source = etree.parse(input)
#   lxml.etree.XMLSyntaxError: invalid character in attribute value, ...
#
# pyhwp 0.1b15 소스(hwp5/xmlformat.py의 xmlevents_to_textchunks)를 직접 열어
# 보고 원인을 확인했다: hwp5txt/hwp5html은 우리가 원본 hwp를 읽어서 바로 최종
# 텍스트/HTML로 바꾸는 게 아니라, 중간 단계로 자기 내부 표현을 XML 파일에
# 먼저 써두고(임시 파일) 그걸 lxml로 다시 읽어들여 XSLT를 돌린다. 그런데 그
# "중간 XML을 쓰는" 코드가 속성값/텍스트에서 NUL(\x00)만 지우고, XML 1.0
# 규격상 원래 못 쓰는 다른 제어문자(0x01~0x08, 0x0B, 0x0C, 0x0E~0x1F 등)는
# 안 지우고 그대로 흘려보낸다. 문서 원문 어딘가(이 파일은 7.8MB로 꽤 큼)에
# 그런 제어문자가 실제로 섞여 있으면, 중간 XML 자체가 XML 1.0 규격을
# 위반하게 되고, 그걸 나중에 lxml이 엄격 모드로 다시 읽다가 위 예외를 던진다.
# 즉 우리 파이프라인 코드의 버그가 아니라 pyhwp 쪽 처리 누락이고, hwp5txt를
# 몇 번을 다시 돌려도 항상 같은 지점에서 죽는다(재시도로 해결 안 됨).
#
# hwp5txt/hwp5html은 우리 파이썬 프로세스가 아니라 별도 서브프로세스로
# 실행되므로, 우리 쪽에서 아무리 몽키패치를 걸어도 그 서브프로세스 안에서
# 새로 import된 pyhwp에는 반영되지 않는다. 그래서 서브프로세스가 이 특정
# 오류로 죽은 게 감지될 때만(stderr에 XMLSyntaxError가 찍힌 경우), pyhwp를
# 우리 프로세스 "안에서" 직접 파이썬 API로 호출한다 - 그 안에서
# xmlevents_to_textchunks를 몽키패치해서 NUL뿐 아니라 문제의 제어문자까지
# 미리 제거해두면, 중간 XML 자체가 처음부터 깨끗하게 만들어지므로 lxml이
# 더 이상 실패하지 않는다(간단한 합성 XML로 재현 테스트 완료 - 패치 전엔
# 동일한 XMLSyntaxError, 패치 후엔 정상 파싱됨).
#
# 이 우회 경로는 정상 경로(hwp5txt/hwp5html 서브프로세스)가 실제로 이
# 특정 오류로 실패한 경우에만, 그 순간에만 지연 임포트/지연 몽키패치로
# 발동한다 - 이미 잘 동작하는 나머지 문서들에는 전혀 관여하지 않으므로
# 안전하다.
_XML_SYNTAX_ERROR_RE = re.compile(r"XMLSyntaxError", re.IGNORECASE)
_INVALID_XML_CHARS_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]"
)


def _looks_like_xml_sanitization_error(stderr: str) -> bool:
    return bool(stderr) and bool(_XML_SYNTAX_ERROR_RE.search(stderr))


# --- hwp5txt 자체가 박아넣는 표/그림 자리표시자 처리 --------------------------
# [2026-08-27 발견] AFSIS 문서 우회 추출 결과를 눈으로 확인하다가, 본문 텍스트
# 앞부분에 "<표>"가 여러 번 연달아 나오는 걸 보고 pyhwp가 쓰는 XSLT
# 스타일시트(hwp5/xsl/plaintext.xsl)를 직접 열어봤다. 거기 이런 템플릿이
# 있다:
#   <xsl:template match="TableControl">&#10;&lt;표&gt;&#10;</xsl:template>
#   <xsl:template match="GShapeObjectControl">&#10;&lt;그림&gt;&#10;</xsl:template>
# 즉 hwp5txt(본문 텍스트만 뽑는 변환)는 문서 흐름 중에 표나 그림을 만나면
# 그 내용을 뽑는 게 아니라 "<표>"/"<그림>"이라는 문자 그대로를 자리표시자로
# 박아넣게 pyhwp 자체가 그렇게 설계돼 있다 - 우리 코드가 만든 것도, 실제
# 표/그림 내용도 아니다.
#
# [2026-08-28 변경] "<그림>"은 여전히 여기서 지운다 - 우리 파이프라인이
# 이미지 내용을 뽑지 않으므로(OCR 경로 없음) 남겨봐야 "그림이 있었다"는 것
# 외엔 알려주는 정보가 없고 검색/임베딩 입장에선 의미 없는 토큰만 늘리는
# 노이즈다.
#
# 그런데 "<표>"는 더 이상 여기서 지우지 않는다. 예전엔 "표 내용은 어차피
# merge_text._flatten_tables()가 문서 맨 끝에 전부 몰아서 이어붙이니까,
# 본문 중간에 남은 <표>는 100% 중복 노이즈"라고 봤는데, 이 가정 때문에
# 생긴 문제를 발견했다(한빈 지적, 2026-08-28): 표 내용을 문서 끝에 몰아
# 붙이면 "6. 추진일정" 같은 제목이나 바로 앞 문맥이 사라져서, 표 내용만
# 담긴 chunk가 어떤 섹션의 표였는지 알 수 없게 된다. 그래서 이제
# merge_text.merge_one()이 이 "<표>" 줄의 위치를 이정표 삼아 표 내용을
# 원래 있던 자리에 그대로 끼워넣는다(제목 바로 다음 같은 원래 문맥 유지) -
# 그래서 이 단계에서 지우면 안 되고, merge_text.py까지 살려서 넘겨야 한다.
# (자리표시자 개수와 실제 추출된 표 개수가 안 맞는 예외적인 경우엔
# merge_text.py가 안전하게 폴백해서 그때는 이 줄도 지우고 예전처럼 문서
# 끝에 표를 몰아 붙인다 - merge_text._strip_table_placeholders/
# _insert_tables_inline 참고.)
_IMAGE_PLACEHOLDER_LINE_RE = re.compile(r"^<그림>$")


def _strip_hwp5txt_placeholders(text: str) -> str:
    """"<그림>" 자리표시자 줄만 지운다. "<표>"는 지우지 않는다(위 2026-08-28
    변경 설명 참고) - merge_text.py가 그 위치를 보고 표 내용을 원래 자리에
    끼워넣거나, 실패 시 안전하게 지우고 폴백한다."""
    if not text:
        return text
    lines = text.split("\n")
    kept = [ln for ln in lines if not _IMAGE_PLACEHOLDER_LINE_RE.match(ln.strip())]
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _patch_pyhwp_xml_sanitization() -> None:
    """hwp5.xmlformat.xmlevents_to_textchunks을 "NUL뿐 아니라 XML 1.0에서
    금지된 다른 제어문자까지 전부 제거하는 버전"으로 몽키패치한다.

    이미 패치가 적용된 상태면 아무것도 하지 않는다(idempotent) - 같은
    프로세스 안에서 이 우회 경로를 여러 문서에 대해 반복 호출해도 매번
    다시 패치하지 않도록. 원본 함수(hwp5/xmlformat.py)와 완전히 동일하되
    _INVALID_XML_CHARS_RE.sub('', ...) 두 줄만 추가됐다."""
    import hwp5.xmlformat as _xmlformat

    if getattr(_xmlformat, "_rfp_control_char_patch_applied", False):
        return

    from xml.sax.saxutils import escape, quoteattr

    from hwp5.binmodel import Text
    from hwp5.treeop import ENDEVENT, STARTEVENT

    def _patched_xmlevents_to_textchunks(xmlevents):
        entities = {"\r": "&#13;", "\n": "&#10;", "\t": "&#9;"}
        for event, item in xmlevents:
            if event is STARTEVENT:
                yield "<"
                yield item[0]
                for n, v in item[1].items():
                    yield " "
                    yield n
                    yield "="
                    v = quoteattr(v, entities)
                    v = v.replace("\x00", "")
                    v = _INVALID_XML_CHARS_RE.sub("", v)  # <- 원본에 없던 추가 정제
                    yield v
                yield ">"
            elif event is Text:
                text = escape(item)
                text = text.replace("\x00", "")
                text = _INVALID_XML_CHARS_RE.sub("", text)  # <- 원본에 없던 추가 정제
                yield text
            elif event is ENDEVENT:
                yield "</"
                yield item
                yield ">"

    _xmlformat.xmlevents_to_textchunks = _patched_xmlevents_to_textchunks
    _xmlformat._rfp_control_char_patch_applied = True


def _bypass_extract_text(hwp_path: Path) -> tuple[str, str | None]:
    """hwp5txt CLI(서브프로세스) 대신 pyhwp를 같은 프로세스 안에서 직접
    호출해 본문 텍스트를 추출한다. 위 몽키패치가 먼저 적용되므로 중간
    XML의 제어문자가 미리 제거된 상태로 만들어진다. 반환: (text, error)."""
    try:
        _patch_pyhwp_xml_sanitization()
        from hwp5.hwp5txt import TextTransform
        from hwp5.xmlmodel import Hwp5File

        with closing(Hwp5File(str(hwp_path))) as hwp5file:
            buf = io.BytesIO()
            transform = TextTransform().transform_hwp5_to_text
            transform(hwp5file, buf)
            return buf.getvalue().decode("utf-8", errors="replace"), None
    except Exception as e:  # noqa: BLE001 - 우회 경로라 어떤 예외가 날지 예측하기 어려움
        return "", f"{type(e).__name__}: {e}"


# --- 표 셀 배경색(셀 "칠해짐") 추출 -----------------------------------------
# [2026-08-28 신설] "추진일정" 같은 Gantt형 표를 실제 원본 문서(국방과학연구소_
# 기록관리시스템 통합 활용 및 보안 환경 구축.hwp)로 진단(scripts/
# diagnose_gantt_table_shading*.py)해서 확인한 내용: hwp5html이 표 셀에
# 배경색을 줄 때, 그 값이 셀 태그 자체의 style 속성("background-color:...")
# 으로 직접 붙는 경우도 있고, class 속성(예: "borderfill-93")으로만 표시되고
# 실제 색상값은 같이 생성되는 CSS 파일(styles.css)의 ".borderfill-93 {
# background-color: #a3d7dd; ... }" 규칙에 있는 경우도 있다. 그래서 셀의
# 배경색을 알아내려면 style 속성과 class->CSS 매핑 둘 다 확인해야 한다.
_BG_COLOR_DECL_RE = re.compile(r"background-color\s*:\s*([^;]+)")


def _parse_css_background_colors(css_text: str) -> dict[str, str]:
    """CSS 텍스트에서 클래스 선택자(".borderfill-93" 등) -> background-color
    값만 뽑아 {"borderfill-93": "#a3d7dd", ...} 형태로 반환한다(선행 마침표는
    뗀다). hwp5html이 만드는 styles.css는 복잡한 선택자 조합 없이 단순 클래스
    선택자 위주라, 제대로 된 CSS 파서 없이 "{"/"}" 로만 블록을 나눠도 충분하다
    (실제 국방과학연구소 문서의 styles.css로 검증됨)."""
    mapping: dict[str, str] = {}
    for block in css_text.split("}"):
        if "{" not in block:
            continue
        selector, body = block.split("{", 1)
        selector = selector.strip()
        if not selector.startswith("."):
            continue
        m = _BG_COLOR_DECL_RE.search(body)
        if m:
            mapping[selector[1:]] = m.group(1).strip()
    return mapping


def _cell_bg_color(cell_tag, css_bg: dict[str, str]) -> str | None:
    """셀 태그 하나의 배경색을 style 속성(우선) 또는 class -> CSS 매핑
    (차선)으로 알아낸다. 둘 다 없으면 배경색 지정이 없는 것(보통 흰색/기본값)
    이라 None."""
    style = cell_tag.get("style") or ""
    m = _BG_COLOR_DECL_RE.search(style)
    if m:
        return m.group(1).strip()
    cls = cell_tag.get("class")
    if cls:
        cls_key = cls if isinstance(cls, str) else " ".join(cls)
        for one in cls_key.split():
            if one in css_bg:
                return css_bg[one]
    return None


def _int_attr(tag, name: str, default: int = 1) -> int:
    val = tag.get(name)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _top_level_tables(soup) -> list:
    """soup 안의 <table> 중, 다른 <table> 안에 중첩되지 않은 것만 반환한다.

    [2026-08-28 발견] 국방과학연구소_기록관리시스템 통합 활용 및 보안 환경
    구축.hwp 문서를 진단(scripts/diagnose_table_placeholder_mismatch.py)해서
    실제로 확인한 문제: hwp5html이 만드는 XHTML에서 BeautifulSoup의
    find_all("table")은 표 안에 중첩된 표까지 별도 표로 재귀적으로 다 찾아서
    센다(이 문서에서 표 111개 중 2개가 다른 표 안에 중첩돼 있었음). 반면
    hwp5txt(본문 텍스트)가 남기는 "<표>" 자리표시자는 최상위 표 개수(109개)
    와 정확히 일치했다 - merge_text._insert_tables_inline()이 이 두 개수가
    같아야만 표 내용을 원래 자리에 끼워넣으므로, 중첩 표까지 표로 세면 개수가
    안 맞아서 표 111개짜리 문서 전체가 "위치 복원 실패 -> 문서 끝 일괄 첨부"
    폴백으로 떨어졌다. 최상위 표만 세면 자리표시자 개수와 정확히 일치해서
    inline 삽입이 정상 동작한다."""
    return [t for t in soup.find_all("table") if t.find_parent("table") is None]


def _direct_rows(table_tag) -> list:
    """table_tag에 직접 속한 <tr>만 반환한다(중첩된 표 안의 <tr>는 제외).

    tbody 유무와 무관하게, 그 <tr>의 가장 가까운 <table> 조상이 정확히
    table_tag 자신인 것만 고른다 - find_all("tr")은 기본이 재귀 탐색이라
    중첩된 표의 행까지 이 표의 행인 것처럼 섞여 들어가는 걸 막기 위함이다.
    중첩된 표의 실제 텍스트 내용은 사라지지 않는다 - 그 표를 담고 있는
    바깥쪽 셀의 get_text()가 어차피 그 안의 텍스트까지 재귀적으로 다
    포함하기 때문에(구조/행 경계는 잃지만 내용 자체는 그 셀 텍스트 안에
    그대로 남음), 별도 표로 다시 뽑아서 중복시키지만 않으면 된다."""
    return [tr for tr in table_tag.find_all("tr") if tr.find_parent("table") is table_tag]


def _direct_cells(tr_tag) -> list:
    """tr_tag에 직접 속한 <td>/<th>만 반환한다(_direct_rows와 같은 이유 -
    이 행의 셀 하나가 중첩 표를 담고 있으면 find_all(["td","th"])이 그
    안쪽 표의 셀까지 재귀적으로 다 끌고 오는 걸 막는다)."""
    return [c for c in tr_tag.find_all(["td", "th"]) if c.find_parent("tr") is tr_tag]


def _extract_table_rows(table_tag, css_bg: dict[str, str]) -> list[list[TableCell]]:
    rows = []
    for tr in _direct_rows(table_tag):
        cells = [
            TableCell(
                text=c.get_text(strip=True),
                colspan=_int_attr(c, "colspan"),
                rowspan=_int_attr(c, "rowspan"),
                bg_color=_cell_bg_color(c, css_bg),
            )
            for c in _direct_cells(tr)
        ]
        if cells:
            rows.append(cells)
    return rows


def _bypass_extract_tables(hwp_path: Path) -> tuple[list[list[list[TableCell]]], str | None]:
    """hwp5html CLI 대신 pyhwp를 직접 호출해 표를 추출한다.
    _bypass_extract_text와 같은 몽키패치를 재사용한다. 반환: (tables, error).

    [2026-08-28 변경] 셀 배경색까지 살리는 정상 경로(extract_tables())와
    달리, 이 경로는 파일로 안 쓰고 메모리 안에서 XHTML 하나만 바로 변환하는
    hwp5의 transform_hwp5_to_xhtml API를 쓰기 때문에 별도 CSS 파일에는
    접근할 수 없다 - xhtml 안에 <style> 태그로 규칙이 인라인돼 있으면 그것만
    파싱해서 쓰고, 없으면 배경색 정보 없이(bg_color=None) 텍스트/구조만
    추출한다. 이 경로는 극소수 손상 문서에서만 쓰이는 우회 경로라 이 정도
    저하는 감수한다."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return [], "beautifulsoup4 미설치로 표 추출 생략"

    try:
        _patch_pyhwp_xml_sanitization()
        from hwp5.hwp5html import HTMLTransform
        from hwp5.xmlmodel import Hwp5File

        with closing(Hwp5File(str(hwp_path))) as hwp5file:
            buf = io.BytesIO()
            transform = HTMLTransform().transform_hwp5_to_xhtml
            transform(hwp5file, buf)
            xhtml = buf.getvalue().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"

    soup = BeautifulSoup(xhtml, "lxml-xml")
    css_bg: dict[str, str] = {}
    for style_tag in soup.find_all("style"):
        css_bg.update(_parse_css_background_colors(style_tag.get_text()))

    tables = []
    for table_tag in _top_level_tables(soup):
        rows = _extract_table_rows(table_tag, css_bg)
        if rows:
            tables.append(rows)
    return tables, None


def extract_text(hwp_path: Path) -> tuple[str, str | None, str | None]:
    """hwp5txt로 본문 텍스트만 추출. 반환: (text, error, note).

    note는 정상 경로면 항상 None이고, 위 "우회 추출"이 실제로 쓰여서
    성공했을 때만 그 사실을 설명하는 문자열이 채워진다(merge_text.py에서
    parse_note에 그대로 남겨 나중에 어떤 문서가 우회 경로를 탔는지 추적할
    수 있게 한다)."""
    try:
        proc = _run(["hwp5txt", str(hwp_path)])
    except FileNotFoundError:
        return "", "hwp5txt 실행파일을 찾을 수 없음 (pyhwp 미설치)", None
    except subprocess.TimeoutExpired:
        return "", "hwp5txt 타임아웃", None

    if proc.returncode != 0:
        if _looks_like_xml_sanitization_error(proc.stderr):
            text, bypass_err = _bypass_extract_text(hwp_path)
            if text:
                return (
                    _strip_hwp5txt_placeholders(text),
                    None,
                    "hwp5txt 서브프로세스가 문서 안 제어문자 때문에 XML 파싱에 실패해서, "
                    "pyhwp를 프로세스 안에서 직접 호출하는 우회 경로로 재추출함",
                )
            return (
                "",
                f"hwp5txt 실패(문서 안 제어문자로 인한 XML 파싱 오류) + 우회 추출도 실패: {bypass_err}",
                None,
            )
        return "", f"hwp5txt 실패: {proc.stderr.strip()[:300]}", None
    return _strip_hwp5txt_placeholders(proc.stdout), None, None


def extract_tables(
    hwp_path: Path, timeout: int = 240
) -> tuple[list[list[list[TableCell]]], str | None, str | None]:
    """hwp5html로 변환 후 <table> 태그를 파싱해 표 구조 추출.

    timeout: hwp5html 변환 제한시간(초). 원래 실측(1.3MB 문서 44.2초)을
    기준으로 90초로 올렸었는데, [2026-08-28] 98건 전체 재파싱에서 35건
    (36%)이 90초 타임아웃에 걸리는 게 확인돼(모듈 docstring 참고) 240초로
    다시 올렸다. 진단 목적으로 더 크게 줄 수도 있다(예:
    diagnose_hwp_table.py에서 300초로 호출).

    [2026-08-28 변경] 셀 텍스트뿐 아니라 배경색(style 속성 직접 지정 또는
    class -> 같이 생성되는 styles.css 매핑)과 colspan/rowspan까지 같이
    뽑는다(TableCell). "추진일정" 같은 Gantt형 표는 활성 기간이 셀 텍스트가
    아니라 배경색으로만 표시돼서, 이 정보가 없으면 merge_text.py가 그런
    표를 복원할 방법이 없다 - scripts/diagnose_gantt_table_shading*.py로
    실제 문서(국방과학연구소_기록관리시스템 통합 활용 및 보안 환경
    구축.hwp)에서 이 배경색 인코딩 방식을 확인하고 반영했다.

    반환: (tables, error, note). note는 extract_text와 마찬가지로 우회
    추출이 실제로 쓰였을 때만 채워진다."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return [], "beautifulsoup4 미설치로 표 추출 생략", None

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "out"
        try:
            proc = _run(["hwp5html", "--output", str(out_dir), str(hwp_path)], timeout=timeout)
        except FileNotFoundError:
            return [], "hwp5html 실행파일을 찾을 수 없음", None
        except subprocess.TimeoutExpired:
            return [], "hwp5html 타임아웃", None

        if proc.returncode != 0:
            if _looks_like_xml_sanitization_error(proc.stderr):
                tables, bypass_err = _bypass_extract_tables(hwp_path)
                if tables:
                    return (
                        tables,
                        None,
                        "hwp5html 서브프로세스가 문서 안 제어문자 때문에 XML 파싱에 실패해서, "
                        "pyhwp를 프로세스 안에서 직접 호출하는 우회 경로로 재추출함",
                    )
                if bypass_err:
                    return (
                        [],
                        f"hwp5html 실패(문서 안 제어문자로 인한 XML 파싱 오류) + 우회 표 추출도 실패: {bypass_err}",
                        None,
                    )
                return (
                    [],
                    "hwp5html 실패(문서 안 제어문자로 인한 XML 파싱 오류) - 우회 경로는 성공했지만 표가 0개",
                    None,
                )
            return [], f"hwp5html 실패: {proc.stderr.strip()[:300]}", None

        index_html = out_dir / "index.xhtml"
        if not index_html.exists():
            candidates = list(out_dir.glob("*.xhtml")) + list(out_dir.glob("*.html"))
            if not candidates:
                return [], "hwp5html 출력 파일을 찾지 못함", None
            index_html = candidates[0]

        # hwp5html의 출력은 XHTML(XML)이라 "lxml"(HTML 파서) 대신 "lxml-xml"을 써야
        # XMLParsedAsHTMLWarning 없이 더 안정적으로 파싱된다.
        soup = BeautifulSoup(index_html.read_text(encoding="utf-8", errors="ignore"), "lxml-xml")

        # 셀 배경색 판별용 CSS 매핑을 만든다 - hwp5html --output은 index.xhtml과
        # 같은 폴더에 styles.css(또는 유사한 이름)를 같이 만든다(diagnose_gantt_
        # table_shading.py로 실제 확인: 'styles.css' 1개). <style> 인라인 태그가
        # 있으면 그것도 같이 반영한다(방어적으로 - 정상 경로에서 흔치는 않음).
        css_bg: dict[str, str] = {}
        for css_path in out_dir.glob("*.css"):
            css_bg.update(_parse_css_background_colors(css_path.read_text(encoding="utf-8", errors="ignore")))
        for style_tag in soup.find_all("style"):
            css_bg.update(_parse_css_background_colors(style_tag.get_text()))

        tables = []
        for table_tag in _top_level_tables(soup):
            rows = _extract_table_rows(table_tag, css_bg)
            if rows:
                tables.append(rows)
        return tables, None, None


def parse_hwp(hwp_path: Path, extract_tables_flag: bool = True, tables_timeout: int = 240) -> ParseResult:
    """기본값 True: hwp5html로 표까지 뽑는다. 문서당 최대 240초씩 더 걸릴 수 있지만
    (모듈 docstring의 2026-08-28 추가 - 90초로는 36%가 타임아웃 나는 게 확인됨),
    실제로 표를 정상적으로 찾아낸다는 게 확인됐다. 텍스트만 빠르게 필요하면
    extract_tables_flag=False로 끌 것.

    tables_timeout: extract_tables()에 그대로 전달되는 표 추출 타임아웃(초).
    [2026-08-28 추가] 240초로 올린 뒤에도 일부 문서(3건, scripts/
    reextract_slow_tables.py 참고)는 여전히 타임아웃이 났다 - 전체 98건을
    다시 재파싱하는 대신 이 몇 건만 훨씬 큰 타임아웃(예: 600초)으로 개별
    재시도할 수 있게 노출해뒀다."""
    hwp_path = Path(hwp_path)
    if not hwp_path.exists():
        return ParseResult(error=f"파일 없음: {hwp_path}")

    text, text_err, text_note = extract_text(hwp_path)
    if extract_tables_flag:
        tables, table_err, table_bypass_note = extract_tables(hwp_path, timeout=tables_timeout)
        if table_err:
            table_note = f"표 추출 실패: {table_err}"
        elif not tables:
            table_note = "표 추출 시도함 - hwp5html 변환은 성공했지만 <table> 태그를 찾지 못함(문서에 표가 없거나 TableControl 형태가 아닐 수 있음)"
        else:
            table_note = f"표 {len(tables)}개 추출 성공"
        if table_bypass_note:
            table_note += f" | {table_bypass_note}"
    else:
        tables, table_err, table_note = [], None, "표 추출 비활성화(extract_tables_flag=False)"

    if text_err and not text:
        # 텍스트 추출 자체가 실패하면 문서 파싱 실패로 간주
        return ParseResult(error=text_err, text_note=text_note)

    return ParseResult(
        text=text,
        tables=tables,
        n_tables=len(tables),
        source="hwp5",
        error=None,
        table_note=table_note,
        text_note=text_note,
    )


if __name__ == "__main__":
    import sys
    result = parse_hwp(Path(sys.argv[1]))
    print("ok:", result.ok, "| 텍스트 길이:", len(result.text), "| 표 개수:", result.n_tables)
    if result.error:
        print("에러:", result.error)
    if result.text_note:
        print("본문 우회 메모:", result.text_note)
