"""24단계(신규): "서빙 화면에서 미리 눌러볼 수 있는 버튼"(예산/일정/신청 서식
+ 그 외 자주 물어볼 만한 항목들) 프로토타입 - 몇 개 문서로 감을 잡아본다.

[배경 - 2026-09-08] 예산/일정은 이미 전처리 단계(load_metadata.py/merge_text.py)
에서 정제해둔 컬럼이 있어서 거의 그대로 쓸 수 있다. "신청 서식"은 두 가지를
같이 보여준다: (1) 제안서 작성 규정 텍스트, (2) 첨부 서식 목록.

[배경 - 2026-09-08 추가] golden set 180문항(공식 111 + rag-56 + set-13) 전체를
주제별로 훑어본 결과, 예산/일정 다음으로 자주 물어볼 만한 주제들을 추가로
뽑았다: 하도급/공동수급(15건, 8% - 가장 큼), 사업목적/내용요약(13건, 7%),
평가배점/비율(9건, 5%), 계약방식/낙찰방식(8건, 4%), 참가자격/제한(8건, 4%),
계약보증금/하자보증금(6건, 3%), 발주기관(4건, 2%), 문의처/연락처(2건, 1%).
이번 단계에서 이 항목들의 추출기를 전부 프로토타입으로 만든다 - 단, 평가배점/
비율은 배점표가 보통 표 형태라 정규식으로는 "배점표가 있다는 것을 찾는" 정도만
가능하고 실제 배점 숫자까지 정확히 뽑아내긴 어려울 걸로 예상된다(아래 함수
docstring에도 별도로 표시해둠) - 표 형태 데이터라 이 항목만큼은 LLM 요약이나
표 구조 파싱 쪽으로 넘어가는 게 나을 수 있다는 점을 결과를 보고 판단해야 한다.

API 호출 없음(임베딩/생성 둘 다 안 씀 - 순수 텍스트/정규식 처리라 0원, 즉시
실행됨). output/merged_docs.pkl 캐시만 있으면 된다.

주의 - 이 스크립트를 클라우드 샌드박스에서 미리 돌려보고 로직을 다듬었는데,
샌드박스 코퍼스(98건, 전부 n_tables=0 - 표가 있는 문서가 하나도 없는 비대표
표본)로는 "작성 규정 텍스트"류 추출이 잘 안 맞는 경우가 많았다(목차와 본문을
구분하기 어려움). 실제 코퍼스(표 있는 문서 다수 포함)에서 돌리면 결과가
많이 다를 수 있다 - 그래서 아래 함수들은 기존 예산/마감일 처리와 같은 철학으로
"후보를 여러 개 보여주고 사람이 고르게" 만들었다(자동으로 하나만 확정하지
않음). 실제로 돌려보고 후보 품질을 같이 봐야 다음 단계(정규식 다듬기 또는
LLM으로 조문 추출하는 방식으로 전환)를 정할 수 있다.

기본으로는 이번 세션에서 이미 내용을 확인한 문서 몇 개(부산관광공사 등)를
예시로 넣어뒀다. 다른 문서를 보고 싶으면 --n 인자로 개수를 지정하면 코퍼스
앞쪽 N건을 대신 사용한다.

사용법:
    python scripts/step24_prefilled_qa_prototype.py            # 기본 예시 문서
    python scripts/step24_prefilled_qa_prototype.py --n 10      # 코퍼스 앞쪽 10건
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_processing.load_metadata import load_clean_metadata  # noqa: E402
from src.data_processing.merge_text import load_merged, merge_all, save_merged  # noqa: E402

# 이번 세션에서 이미 내용을 직접 확인한 문서들(존재하면 우선 사용) - 결과가
# 그럴듯한지 바로 눈으로 비교하기 좋게 하려고 골랐다.
DEFAULT_DOC_IDS = [
    "부산관광공사_경영정보시스템 기능개선.hwp",
    "한국농어촌공사_네팔 수자원관리 정보화사업-Pilot 시스템 구축용역.hwp",
    "한국철도공사 (용역)_[재공고][긴급][협상형]운행정보기록 자동분석시스.hwp",
]

# --- 후보 구간 경계 다듬기 -----------------------------------------------
# [2026-09-10 추가] 지금까지 후보는 전부 "매치 위치에서 N자"처럼 글자 수로 잘라서
# 만들었다. 그러다 보니 화면에 나오는 후보가 단어 중간에서 시작하거나("...작성
# 지침을" -> "성지침을") 문장이 끝나기 전에 잘려서("...제안요청서에서") 읽다가
# 끊기는 문제가 있었다. 한빈이 실제 화면에서 발견.
#
# 청크 문제가 아니라 이 슬라이싱 문제라서, 원문(row["text"])이 그대로 있는 이상
# 굳이 LLM한테 "이거 잘린 것 같아?"를 물을 필요가 없다 - 양 끝을 자연스러운
# 경계까지 밀어주면 결정적으로(추가 비용/지연 없이) 해결된다.
#
# 왼쪽: 최대 _SNAP_BACK자까지 거슬러 올라가 줄 시작(없으면 단어 경계)으로 맞춘다.
# 오른쪽: 최대 _SNAP_FORWARD자까지 내려가며 아래 중 첫 경계에서 끊는다.
#   - 마침표류 뒤 공백 (앞이 숫자면 제외 - "1. 사업개요"의 번호에서 끊기지 않게)
#   - 한국어 종결어미(함/음/임/됨/다/요...) 뒤 줄바꿈
#   - 새 항목이 시작되는 줄바꿈(불릿/번호 앞) - 문장 중간에서 줄만 바뀐 경우
#     (hwp/pdf 줄바꿈)에는 걸리지 않고 지나가므로 오히려 잘 맞는다
#   - 빈 줄
# 경계를 못 찾으면 마지막 공백에서 자르고 "…"를 붙여 잘렸다는 걸 표시한다.
#
# 후보의 "개수"나 "찾았는지 여부"는 이 변경과 무관하다(정규식 매치 위치는 그대로).
# 즉 step25 히트율 수치에는 영향이 없고, 화면에 보이는 텍스트만 읽기 좋아진다.
_SNAP_BACK = 120
_SNAP_FORWARD = 300
_NATURAL_END_PAT = re.compile(
    r"(?<!\d)[.!?]\s"
    r"|(?:함|음|임|됨|다|요|권고|준수)\s*\n"
    r"|\n\s*(?=[-–▢○□※◦·▪◆■]|\d+[.)]|\(\d+\)|[가-힣]\.)"
    r"|\n\s*\n"
)


def _snap_span(text: str, start: int, end: int, max_forward: int = _SNAP_FORWARD) -> tuple[str, int, int]:
    """글자 수로 자른 [start, end) 구간의 양 끝을 자연스러운 경계로 민다.

    반환값은 (다듬은 텍스트, 실제 시작 위치, 실제 끝 위치). 위치까지 같이
    돌려주는 이유는 서빙 화면의 "원문 더 보기"(이 후보 주변을 더 넓게 다시
    보여주기) 때문이다 - 텍스트만 있으면 원문에서 다시 찾아야 하는데,
    표준 서식처럼 같은 문구가 여러 번 나오는 문서에서는 엉뚱한 위치를
    집을 수 있어서 처음부터 위치를 들고 다니는 게 정확하다.

    max_forward를 줄이면 뒤쪽을 덜 확장한다 - 연락처처럼 필요한 정보가 매치
    지점에서 바로 끝나는 항목은 길게 끌고 오면 오히려 잡음이 섞인다.
    """
    start = max(0, start)
    end = min(len(text), end)

    if start > 0:
        head_zone_start = max(0, start - _SNAP_BACK)
        nl = text.rfind("\n", head_zone_start, start)
        if nl != -1:
            start = nl + 1
        else:
            sp = text.rfind(" ", head_zone_start, start)
            if sp != -1:
                start = sp + 1

    if end < len(text):
        tail_zone = text[end:end + max_forward]
        m = _NATURAL_END_PAT.search(tail_zone)
        if m:
            end += m.end()
        else:
            sp = text.rfind(" ", end - 60, end)
            if sp != -1:
                end = sp
            return text[start:end].strip() + "…", start, end

    return text[start:end].strip(), start, end


def _snap_bounds(text: str, start: int, end: int, max_forward: int = _SNAP_FORWARD) -> str:
    """_snap_span()의 텍스트만 필요한 곳을 위한 얇은 래퍼."""
    return _snap_span(text, start, end, max_forward)[0]


def _candidate(method: str, text: str, start: int, end: int, max_forward: int = _SNAP_FORWARD) -> dict:
    """후보 dict를 만든다. text 외에 원문 위치(start/end)도 같이 담는다 -
    기존 소비자(step25 히트율, step26 화면)는 method/text만 읽으므로 영향 없음."""
    snippet, s, e = _snap_span(text, start, end, max_forward)
    return {"method": method, "text": snippet, "start": s, "end": e}


# --- 신청 서식: 첨부 서식 목록 (예: [별지서식 1호], [붙임 1], 【서식 제2호】) ---
_FORM_HEADER_PAT = re.compile(r"[\[【]\s*(?:별지\s*)?(?:서식|별첨|붙임)\s*(?:제\s*)?\d+\s*(?:호)?\s*[\]】]")


def extract_form_list(text: str, max_items: int = 15) -> list[dict]:
    """문서 안에 번호가 매겨진 첨부 서식 헤더를 순서대로(중복 제거) 뽑는다.

    [2026-09-08 수정] 처음엔 헤더 "바로 뒤" 텍스트만 잘라 제목으로 추측했는데,
    실제 코퍼스로 돌려보니 두 가지 문제가 나왔다: (1) 표로 정리된 문서(예:
    부산관광공사)는 제목이 헤더 뒤가 아니라 앞(표의 다른 칸)에 있어서 "1부"
    같은 부수 표기만 잡혔고, (2) 목차에 공백/탭 구분자가 아예 없는 문서(예:
    한국농어촌공사, "일반현황86[별지서식 제2호]"처럼 페이지 번호와 다음 헤더가
    붙어서 나옴)는 다음 헤더 시작 전까지 안 끊겨서 여러 항목이 뒤섞여 잡혔다.
    "제목을 하나로 확정해서 추측"하는 대신, 헤더 앞/뒤 짧은 문맥을 그대로
    같이 보여주는 쪽으로 바꿨다 - 예산/마감일 후보와 같은 철학(자동 확정 대신
    후보 문맥만 노출, 사람이 보고 판단)."""
    items: list[dict] = []
    seen_labels: set[str] = set()
    # 다음 헤더가 시작되는 지점(숫자+"[" 조합 포함)까지도 끊는 지점으로 취급 -
    # 공백/탭 구분자가 없는 문서에서 다음 항목과 섞이는 걸 막기 위함.
    stop_pat = re.compile(r"\t|\s{2,}|[-–]\s*\d{1,3}\b|\d{1,3}\s*[\[【]|[\[【]")
    for m in _FORM_HEADER_PAT.finditer(text):
        label = re.sub(r"\s+", " ", m.group(0)).strip()
        if label in seen_labels:
            continue
        seen_labels.add(label)
        before = text[max(0, m.start() - 40):m.start()].replace("\n", " ").strip()
        tail = text[m.end():m.end() + 60]
        stop = stop_pat.search(tail)
        after = (tail[:stop.start()] if stop else tail).replace("\n", " ").strip()
        items.append({"label": label, "before": before, "after": after})
        if len(items) >= max_items:
            break
    return items


# --- 신청 서식: 제안서 작성 규정 텍스트 (본문 조문) ---
_SPEC_HEADING_PAT = re.compile(
    r"(제안서\s*작성\s*규격|제안서\s*작성\s*요령|제안서의\s*규격|제출\s*규격|제안서\s*규격|제안서\s*제출\s*형식)"
)
_DASH_NUM_PAT = re.compile(r"[-–]\s*\d{1,3}\b")
_SPEC_FACT_PAT = re.compile(r"A4[^\n]{0,60}(?:이내|규격|매|쪽|장)")


def extract_spec_candidates(text: str, window: int = 400) -> list[dict]:
    """제안서 작성 규정으로 보이는 본문 조각의 후보들을 찾는다. 자동으로
    하나만 확정하지 않고 방법별로 후보를 다 모아서 반환 - 사람이 어느 게
    진짜 본문(목차 아님)인지 확인해야 한다."""
    candidates: list[dict] = []

    # 방법 1: "제안서 작성규격" 류 헤딩을 찾되, 바로 뒤에 "- 52"류 페이지
    # 참조가 밀집돼 있으면(목차 특유의 패턴) 후보에서 제외한다.
    for m in _SPEC_HEADING_PAT.finditer(text):
        tail = text[m.end():m.end() + 180]
        if len(_DASH_NUM_PAT.findall(tail)) >= 2:
            continue
        candidates.append(_candidate("heading", text, m.start(), m.start() + window))

    # 방법 2: 헤딩과 무관하게 "A4 + 분량 단위어" 조합을 본문에서 직접 찾는다
    # (헤딩 문구가 목차에만 있고 본문엔 반복 안 되는 문서에서 더 잘 먹힘).
    m2 = _SPEC_FACT_PAT.search(text)
    if m2:
        start = max(0, m2.start() - 80)
        candidates.append(_candidate("fact_pattern(A4+분량단위어)", text, start, start + window))

    return candidates


# --- 공통 헬퍼: 제목(heading) 패턴 기반 후보 추출 ---
# extract_spec_candidates()가 이미 검증한 "목차 오탐 방지"(제목 뒤에 "- 52"류
# 페이지 참조가 밀집돼 있으면 목차로 보고 제외) 로직을 재사용한다. 새로 추가하는
# 필드(계약방식/하도급/참가자격/사업목적 등)도 전부 "본문 제목 vs 목차" 문제를
# 똑같이 겪을 것으로 예상돼서, 개별 함수마다 같은 로직을 복붙하는 대신 여기로
# 뽑아냈다.
#
# [2026-09-08 수정] 실제 코퍼스로 돌려보니 "하도급/공동수급", "참가자격" 같은
# 키워드는 목차 문제가 아니라 다른 문제를 냈다 - 이 단어들이 표제어가 아니라
# 본문/첨부서식 곳곳(특히 [별지서식] 공동수급표준협정서·서약서 같은 정형 계약서
# 템플릿)에 수십 번씩 반복 등장해서, 문서 하나당 후보가 50~70개씩 쏟아지는
# 사고가 났다(부산관광공사 문서에서 "공동수급" 관련 후보 43개, 봉화군 문서에서
# 30개 이상 등). 대부분은 같은 문단 안에서 몇 백 자 간격으로 겹치는 사실상
# 중복 후보였다. 두 가지로 막았다: (1) 직전에 채택한 후보 위치에서 min_gap
# (기본 window의 절반) 이내면 건너뛴다 - 같은 조항/템플릿 안에서 반복되는
# 건 하나로 합쳐짐, (2) max_items로 문서당 상한을 둔다. 그래도 여러 개가
# 남을 수 있는데, 보통 앞쪽 후보(본문 초반 조항)가 실제 정책을 담고 있고
# 뒤쪽 후보들(특히 "제1조(목적)"류 조항 번호가 섞인 것)은 표준 계약서 서식
# 템플릿 자체일 가능성이 높다는 걸 결과 볼 때 참고할 것.
def _heading_candidates(
    text: str, heading_pat: re.Pattern, window: int = 400, max_items: int = 6, min_gap: int | None = None,
) -> list[dict]:
    if min_gap is None:
        min_gap = window // 2
    candidates: list[dict] = []
    last_start = -min_gap - 1
    for m in heading_pat.finditer(text):
        if m.start() - last_start < min_gap:
            continue
        tail = text[m.end():m.end() + 180]
        if len(_DASH_NUM_PAT.findall(tail)) >= 2:
            continue
        candidates.append(_candidate("heading", text, m.start(), m.start() + window))
        last_start = m.start()
        if len(candidates) >= max_items:
            break
    return candidates


# --- 발주기관: 이미 정제된 메타데이터 컬럼을 그대로 쓴다(추출 불필요) ---
def format_issuer(row) -> str:
    issuer = row.get("발주 기관")
    if issuer and str(issuer) != "nan":
        return f"  발주 기관: {issuer}"
    return "  발주 기관: 정보 없음"


# --- 문의처/연락처 ---
# [2026-09-08 수정] 처음엔 "담당자"도 표제어 후보에 넣었는데, 실제 코퍼스에서
# "담당자"는 연락처 표제어보다 "담당자 매뉴얼은...", "담당자 지출결의서..." 같은
# 시스템 사용자 역할을 가리키는 일반 업무 용어로 훨씬 더 많이 쓰였다 - 요구사항
# 명세 표(SFR-xxx류) 안에서만 수십 번 매치돼 진짜 연락처 정보는 그 사이에
# 묻혀버렸다. "담당자"는 빼고, 실제로 신뢰도가 높았던 전화/팩스 번호 패턴을
# 1순위로 삼고, 그마저 없을 때만 "문의처/연락처/담당부서" 같은 더 구체적인
# 표제어로 보조 탐색하게 바꿨다.
_CONTACT_HEADING_PAT = re.compile(r"담당\s*부서|문의\s*처|문의\s*사항|연락\s*처")
_PHONE_PAT = re.compile(r"(?:TEL|Tel|전화|FAX|Fax|팩스)\s*[:：]?\s*[\d\-()]{7,}")


def extract_contact_candidates(text: str, window: int = 150, max_items: int = 5) -> list[dict]:
    """전화/팩스 번호 형태가 직접 나오는 곳을 1순위 후보로 모으고(가장
    신뢰도가 높음), 그게 하나도 없는 문서에서만 "문의처/연락처" 같은
    표제어 근처를 보조로 찾는다. 같은 담당자 정보가 근처에서 여러 번
    반복되는 걸(TEL 다음 줄에 FAX가 바로 나오는 경우 등) 별개 후보로
    중복 노출하지 않도록 direct 매치끼리도 최소 간격을 둔다."""
    candidates: list[dict] = []
    last_start = -(window)
    for m in _PHONE_PAT.finditer(text):
        if m.start() - last_start < window // 2:
            continue
        start = max(0, m.start() - 40)
        candidates.append(_candidate("phone_pattern", text, start, m.end() + 20, max_forward=80))
        last_start = m.start()
        if len(candidates) >= max_items:
            return candidates
    if candidates:
        return candidates
    for m in _CONTACT_HEADING_PAT.finditer(text):
        candidates.append(_candidate("heading(문의처/연락처)", text, m.start(), m.start() + window))
        if len(candidates) >= max_items:
            break
    return candidates


# --- 계약방식/낙찰방식 ---
_CONTRACT_METHOD_PAT = re.compile(r"계약\s*체결\s*방법|계약\s*방법|계약\s*방식|낙찰자\s*결정\s*방법|낙찰\s*방법")


def extract_contract_method_candidates(text: str) -> list[dict]:
    return _heading_candidates(text, _CONTRACT_METHOD_PAT, window=300, max_items=4)


# --- 하도급/공동수급 - 180문항 분석에서 가장 빈도가 높았던(15건, 8%) 신규 항목.
# 실제 코퍼스에서 후보 폭주가 가장 심했던 항목이라 max_items를 특히 낮게 잡았다
# ([별지서식] 공동수급표준협정서 템플릿 안에서만 같은 단어가 수십 번 반복됨) ---
_SUBCONTRACT_PAT = re.compile(r"하도급|공동수급|공동이행|분담이행|컨소시엄")


def extract_subcontract_candidates(text: str) -> list[dict]:
    return _heading_candidates(text, _SUBCONTRACT_PAT, window=350, max_items=5, min_gap=400)


# --- 참가자격/제한 ---
_ELIGIBILITY_PAT = re.compile(r"입찰\s*참가\s*자격|참가\s*자격|지역\s*제한|참가\s*제한")


def extract_eligibility_candidates(text: str) -> list[dict]:
    return _heading_candidates(text, _ELIGIBILITY_PAT, window=350, max_items=5, min_gap=400)


# --- 계약보증금/하자보증금(비율) ---
# [2026-09-08 수정] 처음엔 키워드 뒤 60자 안에서만 %를 찾았는데, 실제 코퍼스
# (한국농어촌공사 문서)에서 "하자보수보증금률은 「국가계약법」 제18조... 의거
# 1년에 2%를 적용한다"처럼 법령 인용구가 키워드와 % 사이에 끼어들어 60자를
# 넘어가는 실제 사례를 놓쳤다(이 문구는 이전 세션에서 이미 정답으로 확인했던
# 것이라 놓친 게 확실한 회귀임). 탐색 범위를 150자로 넓혔다 - 그만큼 무관한
# %를 잘못 묶을 위험도 있지만, 어차피 "후보만 보여주고 사람이 확인" 방식이라
# 넓게 잡아 후보를 놓치지 않는 쪽이 낫다고 판단. 같은 이유로 다른 항목들처럼
# 겹치는 후보 방지(min_gap)와 상한(max_items)도 같이 추가했다.
#
# [2026-09-09 수정] step25(전체 98건 히트율)에서 계약보증금만 22.4%로 유난히
# 낮게 나와서, step25b로 "단어는 있는데 후보를 못 찾은" 30건을 직접 눈으로
# 봤다. 그중 최소 3건(인천광역시/경상북도 봉화군/서울시립대학교)이 같은
# 패턴이었다: (1) 비율을 "%"가 아니라 "5/100" 같은 분수(또는 "100분의 5")로
# 표기하는데 기존 정규식은 "%/퍼센트/프로"만 찾았음, (2) "입찰금액의 5/100에
# 해당하는 입찰보증금"처럼 숫자가 키워드 "앞"에 오는 경우가 많은데 기존 정규식은
# 키워드 뒤쪽만 봤음, (3) 경상북도 봉화군은 표 형태로 "보증금율"이라는 표제어를
# 썼는데 이 단어 자체가 키워드 목록에 없었음. 세 가지를 다 고쳤다: 분수 표기를
# 비율 표현에 추가, 키워드→비율(정방향)과 비율→키워드(역방향) 양쪽 다 탐색,
# "보증금율/보증금률"을 키워드에 추가. (나머지 5건은 실제로 본문에 비율 숫자
# 없이 법령만 인용하거나 면제 문구뿐이라 후보가 없는 게 맞는 케이스였음 - 이건
# 정규식 문제가 아니라 문서 자체에 정보가 없는 것으로 보고 그대로 둔다.)
#
# [2026-09-09 추가 수정] 위 fix를 실제 코퍼스에 다시 돌려보니(step25b) 61건
# 못 찾음(15건은 단어는 있는데 못 찾음)으로 줄긴 했는데, 그 15건 샘플을 다시
# 보니 서울시립대학교 건이 눈에 띄었다 - 분명 "낙찰금액의 5/100에 해당하는
# 입찰보증금"처럼 역방향 패턴에 맞는 문구인데도 안 잡혔다. 원인을 재구성해서
# 확인해보니, 진단 스크립트가 화면에 보여줄 때만 "\n"을 공백으로 바꿔서
# 출력하다 보니(들여쓰기 있는 PDF 줄바꿈이 스페이스 여러 개처럼 보였음) 실제
# 원문에는 "5/100에" 와 "해당하는" 사이에 진짜 줄바꿈(\n)이 있었던 것으로
# 확인됨(재구성한 텍스트로 직접 테스트해서 확인 완료). 그런데 지금 패턴은
# 헤딩 계열 함수들과 같은 이유로 문단을 넘어가지 않도록 "[^\n]"(줄바꿈 제외)
# 를 쓰고 있어서, 같은 문장이 PDF 줄바꿈으로 쪼개진 이런 흔한 경우를 놓쳤다.
# 보증금 관련 조항은 표제어 탐색과 달리 문장/표 안에서 실제 값을 찾는
# fact_pattern 방식이라 문단 경계를 넘어가도 위험이 적다고 판단해서(어차피
# 탐색 범위가 60~150자로 제한돼 있고, 후보만 보여주고 사람이 확인하는 방식),
# 두 gap 모두 줄바꿈도 포함해서 찾도록("[^\n]" -> "[\s\S]") 바꿨다.
#
# [2026-09-09 세 번째 수정] 위 두 fix를 반영한 뒤 다시 진단(step25b)해보니
# 15건 샘플 중 서영대학교 건이 새로 눈에 띔 - "보증금률:％...(입찰금액의
# 1000분의 25이상)"인데, "N분의 M" 표기를 분모 100짜리만 인식하고 있어서
# 분모가 1000인 천분율(퍼밀) 표기를 놓쳤다. 재구성한 실제 문장으로 테스트해서
# 확인 후, "100분의/1000분의"와 "/100, /1000" 둘 다 인정하도록 분모를
# 일반화했다. 겸사겸사 이 문서에서 "％"(전각 퍼센트 기호, HWP에서 흔함)도
# 눈에 띄어서 ASCII "%"와 같이 인정하도록 추가함(을지대학교 등 나머지 신규
# 샘플은 이번에도 그냥 빈 서식(금액 칸이 비어있고 비율 자체가 안 적힘)이라
# 정규식 문제가 아니라 문서에 정보가 없는 게 맞는 케이스로 확인하고 그대로 둠).
_BOND_KEYWORD_PAT = r"계약보증금|입찰보증금|하자보수보증금|하자보증금|보증금율|보증금률"
# "%/퍼센트/프로"(기존, 전각 "％"도 인정)에 더해 "5/100"·"25/1000" 분수 표기와
# "100분의 5"·"1000분의 25" 표기도 비율로 인정(퍼밀/천분율 표기까지 포함).
_BOND_RATE_PAT = r"%|％|퍼센트|프로|\d+(?:\.\d+)?\s*/\s*(?:100|1000)|(?:100|1000)\s*분의\s*\d+(?:\.\d+)?"
# 정방향: 키워드가 먼저 나오고 그 뒤 150자 안에 비율이 나오는 경우(기존 방식).
# PDF 줄바꿈으로 문장이 쪼개지는 경우도 잡도록 "[\s\S]"(줄바꿈 포함)를 사용.
_BOND_FACT_PAT_FWD = re.compile(rf"(?:{_BOND_KEYWORD_PAT})[\s\S]{{0,150}}(?:{_BOND_RATE_PAT})")
# 역방향: "5/100에 해당하는 입찰보증금"처럼 비율이 먼저 나오고 키워드가 뒤따르는
# 경우 - 실제 사례들을 보면 이 간격은 훨씬 짧아서(대개 10~30자) 60자로 좁게 잡음.
_BOND_FACT_PAT_BWD = re.compile(rf"(?:{_BOND_RATE_PAT})[\s\S]{{0,60}}(?:{_BOND_KEYWORD_PAT})")


def extract_bond_candidates(text: str, window: int = 250, max_items: int = 5, min_gap: int = 200) -> list[dict]:
    """제목이 아니라 "보증금 ... 비율(%/분수)" 조합이 실제로 등장하는 위치를
    직접 찾는다(extract_spec_candidates의 방법 2와 같은 fact_pattern 방식) -
    보증금 관련 조항은 헤딩보다 표/문장 안에 비율 숫자로 바로 나오는 경우가
    많아서. 키워드→비율(정방향), 비율→키워드(역방향) 두 패턴을 같이 찾아
    위치순으로 합친 뒤 겹치는 후보만 걸러낸다."""
    raw_matches = sorted(
        [*_BOND_FACT_PAT_FWD.finditer(text), *_BOND_FACT_PAT_BWD.finditer(text)],
        key=lambda m: m.start(),
    )
    candidates: list[dict] = []
    last_start = -min_gap - 1
    for m in raw_matches:
        if m.start() - last_start < min_gap:
            continue
        start = max(0, m.start() - 40)
        candidates.append(_candidate("fact_pattern(보증금+비율)", text, start, start + window))
        last_start = m.start()
        if len(candidates) >= max_items:
            break
    return candidates


# --- 사업목적/내용요약 - 이미 존재하는 "사업 요약" 메타데이터 컬럼을 1순위로,
# 본문 헤딩 기반 후보를 보조로 같이 보여준다 ---
_PURPOSE_HEADING_PAT = re.compile(r"사업\s*목적|사업\s*개요|과업\s*개요|추진\s*배경")


def extract_purpose_candidates(row, text: str, window: int = 400) -> list[dict]:
    candidates: list[dict] = []
    summary = row.get("사업 요약")
    if summary and str(summary) != "nan":
        candidates.append({"method": "메타데이터(사업 요약 컬럼)", "text": str(summary)})
    candidates.extend(_heading_candidates(text, _PURPOSE_HEADING_PAT, window=window, max_items=4, min_gap=300))
    return candidates


# --- 평가배점/비율 - [주의] 배점표는 보통 표 형태라 정규식으로는 "이 근처에
# 배점표가 있다"는 위치 힌트 정도만 잡을 수 있고, 실제 배점 숫자(예: "기술평가
# 80점 + 가격평가 20점")까지 정확히 뽑아내는 건 이 방식으로는 신뢰하기 어렵다.
# 다른 항목들과 달리 결과 품질이 낮을 걸 예상하고 넣는 best-effort 시도임을
# 결과 확인할 때 감안할 것 - 안 되면 LLM 요약이나 표 구조 파싱으로 전환 검토.
_EVAL_SCORE_PAT = re.compile(r"평가\s*배점|배점\s*표|평가\s*항목\s*및\s*배점|기술\s*평가.{0,10}배점|정성적\s*평가")


def extract_eval_score_candidates(text: str) -> list[dict]:
    return _heading_candidates(text, _EVAL_SCORE_PAT, window=400, max_items=4, min_gap=300)


def format_budget_schedule(row) -> str:
    lines = []
    budget = row.get("사업_금액_정제")
    budget_source = row.get("사업_금액_출처")
    if budget and str(budget) != "nan":
        lines.append(f"  예산: {budget:,.0f}원" if isinstance(budget, (int, float)) else f"  예산: {budget}")
    else:
        cand = row.get("사업_금액_후보텍스트")
        if budget_source == "candidate" and cand:
            lines.append(f"  예산: 확정 안 됨(후보 문구만 있음) - \"{str(cand)[:80]}\"")
        else:
            lines.append(f"  예산: 정보 없음(상태={budget_source})")

    start = row.get("입찰 참여 시작일_dt")
    estimated = row.get("입찰참여시작일_추정")
    deadline = row.get("입찰참여마감일_정제")
    deadline_source = row.get("입찰참여마감일_출처")
    start_str = f"{start}" + ("(추정값 - 공개일자로 대체)" if estimated else "") if start is not None else "정보 없음"
    if deadline and str(deadline) != "NaT":
        deadline_str = f"{deadline}"
    else:
        cand = row.get("입찰참여마감일_후보텍스트")
        deadline_str = f"확정 안 됨(후보 문구만 있음) - \"{str(cand)[:80]}\"" if deadline_source == "candidate" and cand else f"정보 없음(상태={deadline_source})"
    lines.append(f"  참여 시작일: {start_str}")
    lines.append(f"  참여 마감일: {deadline_str}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=None, help="코퍼스 앞쪽 N건을 대신 사용(기본값 대신)")
    args = parser.parse_args()

    merged = load_merged()
    if merged is None:
        print("[step24] 캐시(output/merged_docs.pkl) 없음 -> 재파싱합니다...")
        merged = merge_all(load_clean_metadata())
        save_merged(merged)
        merged = load_merged()

    if args.n:
        target_df = merged.head(args.n)
    else:
        target_df = merged[merged["doc_id"].isin(DEFAULT_DOC_IDS)]
        if target_df.empty:
            print("[step24] 기본 예시 문서를 못 찾음 - 코퍼스 앞쪽 5건으로 대신 보여줍니다.")
            target_df = merged.head(5)

    for _, row in target_df.iterrows():
        text = row.get("text") or ""
        print(f"\n{'=' * 78}\n[{row['doc_id']}]")

        print("\n--- 예산/일정 (기존 정제 컬럼 그대로 사용) ---")
        print(format_budget_schedule(row))

        print("\n--- 발주기관 (기존 정제 컬럼 그대로 사용) ---")
        print(format_issuer(row))

        print("\n--- 신청 서식 (1) 첨부 서식 목록 ---")
        forms = extract_form_list(text)
        if forms:
            for f in forms:
                print(f"  {f['label']}   앞: ...{f['before']}   |   뒤: {f['after']}...")
        else:
            print("  (못 찾음 - 이 문서엔 번호 매겨진 서식이 본문에 없거나 다른 표기 방식일 수 있음)")

        print("\n--- 신청 서식 (2) 제안서 작성 규정 텍스트 후보 ---")
        specs = extract_spec_candidates(text)
        if specs:
            for i, s in enumerate(specs, 1):
                preview = s["text"].replace("\n", " ")[:300]
                print(f"  후보 {i} [{s['method']}]: {preview}...")
        else:
            print("  (못 찾음 - 이 방식으로는 본문에서 작성 규정을 못 찾음, 다른 표현을 쓰는 문서일 수 있음)")

        for section_title, candidates in [
            ("하도급/공동수급 (golden set 신규 항목 중 최빈도, 15건/8%)", extract_subcontract_candidates(text)),
            ("사업목적/내용요약", extract_purpose_candidates(row, text)),
            ("계약방식/낙찰방식", extract_contract_method_candidates(text)),
            ("참가자격/제한", extract_eligibility_candidates(text)),
            ("계약보증금/하자보증금", extract_bond_candidates(text)),
            ("문의처/연락처", extract_contact_candidates(text)),
            ("평가배점/비율 [주의: best-effort, 표 형태라 정규식 신뢰도 낮음]", extract_eval_score_candidates(text)),
        ]:
            print(f"\n--- {section_title} ---")
            if candidates:
                for i, c in enumerate(candidates, 1):
                    preview = c["text"].replace("\n", " ")[:300]
                    print(f"  후보 {i} [{c['method']}]: {preview}...")
            else:
                print("  (못 찾음 - 이 문서엔 해당 내용이 없거나 다른 표현을 쓰는 문서일 수 있음)")

    print(
        f"\n{'=' * 78}\n"
        "주의: '첨부 서식 목록'/'발주기관'/'예산/일정'은 그럴듯하게 나오는 편이지만, "
        "그 외 헤딩/패턴 기반 후보들(제안서 작성 규정, 하도급/공동수급, 계약방식, "
        "참가자격, 보증금, 문의처, 사업목적)은 목차와 본문을 정규식만으로 구분하기 "
        "어려워 후보 품질이 문서마다 들쭉날쭉할 수 있다. 특히 '평가배점/비율'은 배점표가 "
        "보통 표 형태라 이 방식으로는 위치 힌트 정도만 잡힐 뿐 실제 배점 숫자까지는 "
        "신뢰하기 어려울 걸로 예상된다. 여러 문서로 돌려보고 항목별로 후보 품질/못 찾는 "
        "비율 감을 잡은 뒤 - 정규식을 더 다듬을지, 품질이 낮은 항목만 LLM에게 넘길지 "
        "결정하면 될 것 같음."
    )


if __name__ == "__main__":
    main()
