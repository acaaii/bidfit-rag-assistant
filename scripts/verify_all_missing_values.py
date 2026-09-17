"""전체 결측치(35건)를 재파싱된 진짜 본문 기준으로 한 번에 재검증하는 스크립트.

[2026-08-28] diagnose_budget_overrides.py로 budget_overrides.csv의 2건만
검증해봤는데, 한빈이 나머지 결측치도 전부 같은 방식으로 검증하고 싶다고 해서
범위를 넓혔다. `output/missing_data_report.csv`(7절에서 만든 리포트)는 원본
파일이 연결되기 전(CSV 1차 텍스트 기준) 스냅샷이라 지금은 낡았다 - 이 스크립트는
그 리포트를 다시 만드는 대신, 재파싱된 진짜 본문(output/merged_docs.pkl,
source=raw_parsed)을 기준으로 4가지 카테고리를 전부 훑는다.

주의: budget_overrides.csv에 이미 값이 들어간 문서는 `budget_unknown`이 False로
잠겨서 자동 파이프라인상에서는 재검사 대상에서 빠진다(diagnose_budget_overrides.py
에서 확인한 문제) - 그래서 이 스크립트는 override 상태와 무관하게, 원본
data_list.csv의 숫자만 보고 "원래 결측/0원이었던 문서" 목록을 다시 계산해서
전부 재검사한다.

카테고리별 처리:
  1. 사업 금액 결측/0원 (원래 7건) - 재파싱 본문에서 후보 재탐지 + 키워드 주변 본문 전체 노출
  2. 입찰 참여 마감일 결측 (8건) - 재파싱 본문에서 후보 재탐지 + 키워드 주변 본문 전체 노출
  3. 입찰 참여 시작일 추정 (25건) - 통계 기반 추정이라 텍스트 검증 대상은 아니지만,
     혹시 본문에 실제 시작일이 명시돼 있는데 놓친 건 아닌지 가벼운 키워드 스캔만 추가로 수행
  4. 공고 번호 결측 (18건) - 행정 식별자라 본문에서 복구 불가(7절에서 이미 확정된 결론).
     텍스트 검증 대상이 아니므로 목록만 나열하고 넘어감.

결과는 콘솔 출력 + output/missing_value_verification.csv로 저장한다(전체를
엑셀로 훑어보고 싶을 때용). 파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
재파싱은 이미 캐시돼 있으므로(step2/3을 이미 돌렸다면) 새로 오래 기다릴 필요 없다.
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR  # noqa: E402
from src.data_processing.load_metadata import (  # noqa: E402
    _extract_budget_candidate,
    _extract_deadline_candidate,
    load_raw_csv,
)
from src.data_processing.merge_text import load_merged  # noqa: E402

OUT_PATH = OUTPUT_DIR / "missing_value_verification.csv"

_BUDGET_KEYWORDS = ["사업예산", "사업 금액", "사업금액", "사업비", "추정가격"]
_DEADLINE_KEYWORDS = ["마감일시", "마감 일시", "입찰 마감", "제출 마감", "접수 마감", "마감일", "마감"]
_START_KEYWORDS = ["참여 시작", "참가 시작", "접수 시작", "접수기간", "제안서 접수", "시작일"]
_DATE_RE = re.compile(r"\d{4}\s*[.\-]\s*\d{1,2}\s*[.\-]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일")


def _keyword_context(text: str, keywords: list[str], window: int = 200) -> list[str]:
    if not isinstance(text, str):
        return []
    snippets = []
    for kw in keywords:
        start = 0
        while True:
            idx = text.find(kw, start)
            if idx == -1:
                break
            s = max(0, idx - 20)
            e = min(len(text), idx + window)
            snippets.append(text[s:e].replace("\n", " ").replace("\r", " ").strip())
            start = idx + len(kw)
    return snippets


def main():
    raw = load_raw_csv()  # 중복 제외는 이미 적용된 원본 CSV(doc_id 부여 전)
    merged = load_merged()
    if merged is None:
        print("output/merged_docs.pkl 캐시가 없습니다 - step2/step3을 먼저 실행해주세요.")
        return
    merged_by_id = {r["doc_id"]: r for _, r in merged.iterrows()}

    rows_out = []

    # --- 1. 사업 금액 결측/0원 ---
    budget_targets = raw.loc[raw["사업 금액"].isna() | (raw["사업 금액"] <= 1), "파일명"].tolist()
    print("=" * 100)
    print(f"[1] 사업 금액 결측/0원 원본 대상: {len(budget_targets)}건")
    for doc_id in budget_targets:
        r = merged_by_id.get(doc_id)
        print("-" * 100)
        print(f"  {doc_id}")
        if r is None:
            print("    -> merged_docs.pkl에 없음(제외된 중복 문서일 수 있음)")
            rows_out.append({"category": "예산", "doc_id": doc_id, "source": None, "current_override": None,
                              "candidate_classification": None, "candidate_snippet": "merged_docs.pkl에 없음", "action_needed": "N/A"})
            continue
        text = r["text"] if isinstance(r["text"], str) else ""
        override_value = r.get("사업_금액_정제")
        classification, snippet = _extract_budget_candidate(text)
        print(f"    source={r['source']!r}  현재 확정값={override_value!r}  재탐지 분류={classification!r}")
        if snippet:
            print(f"    스니펫: {snippet}")
        contexts = _keyword_context(text, _BUDGET_KEYWORDS)
        for c in contexts[:3]:
            print(f"    본문: ...{c}...")
        action = "값 없음 - 사람이 원본 열어 직접 확인 필요" if classification == "no_info" and not override_value else \
                 ("기존 override 값과 스니펫이 일치하는지 확인" if override_value else "candidate 스니펫 확인 후 budget_overrides.csv에 확정값 기입")
        rows_out.append({"category": "예산", "doc_id": doc_id, "source": r["source"], "current_override": override_value,
                          "candidate_classification": classification, "candidate_snippet": snippet or (contexts[0] if contexts else None),
                          "action_needed": action})

    # --- 2. 입찰 참여 마감일 결측 ---
    deadline_targets = raw.loc[raw["입찰 참여 마감일"].isna(), "파일명"].tolist()
    print()
    print("=" * 100)
    print(f"[2] 입찰 참여 마감일 결측 원본 대상: {len(deadline_targets)}건")
    for doc_id in deadline_targets:
        r = merged_by_id.get(doc_id)
        print("-" * 100)
        print(f"  {doc_id}")
        if r is None:
            print("    -> merged_docs.pkl에 없음(제외된 중복 문서일 수 있음)")
            rows_out.append({"category": "마감일", "doc_id": doc_id, "source": None, "current_override": None,
                              "candidate_classification": None, "candidate_snippet": "merged_docs.pkl에 없음", "action_needed": "N/A"})
            continue
        text = r["text"] if isinstance(r["text"], str) else ""
        current = r.get("입찰참여마감일_정제")
        classification, snippet = _extract_deadline_candidate(text)
        print(f"    source={r['source']!r}  현재 확정값={current!r}  재탐지 분류={classification!r}")
        if snippet:
            print(f"    스니펫: {snippet}")
        contexts = _keyword_context(text, _DEADLINE_KEYWORDS)
        for c in contexts[:3]:
            print(f"    본문: ...{c}...")
        action = "본문에 마감일 언급 없음 - 정말 미정일 가능성" if classification == "no_info" else \
                 ("추후 공지 명시됨 - 조치 불필요" if classification == "announced_later" else
                  "candidate 스니펫 확인 후 deadline_overrides.csv에 확정값 기입")
        rows_out.append({"category": "마감일", "doc_id": doc_id, "source": r["source"], "current_override": current,
                          "candidate_classification": classification, "candidate_snippet": snippet or (contexts[0] if contexts else None),
                          "action_needed": action})

    # --- 3. 입찰 참여 시작일 추정 (가벼운 키워드 스캔만) ---
    start_targets = merged.loc[merged["입찰참여시작일_추정"], "doc_id"].tolist()
    print()
    print("=" * 100)
    print(f"[3] 입찰 참여 시작일 추정(공개일자로 대체) 대상: {len(start_targets)}건 - 통계 기반 추정이라 원래는 텍스트 검증 대상이 아니지만, 혹시 본문에 실제 날짜가 있는데 놓친 건 아닌지만 가볍게 스캔")
    flagged = 0
    for doc_id in start_targets:
        r = merged_by_id.get(doc_id)
        if r is None:
            continue
        text = r["text"] if isinstance(r["text"], str) else ""
        contexts = _keyword_context(text, _START_KEYWORDS)
        has_date_nearby = any(_DATE_RE.search(c) for c in contexts)
        if has_date_nearby:
            flagged += 1
            print("-" * 100)
            print(f"  {doc_id}  (추정값={r.get('입찰 참여 시작일_dt')!r}, 공개일자={r.get('공개 일자_dt')!r})")
            print("    -> 본문에 날짜가 포함된 '시작' 관련 문구 발견, 추정치가 아니라 이 본문 날짜가 맞는지 확인 필요:")
            for c in contexts[:2]:
                print(f"    본문: ...{c}...")
            rows_out.append({"category": "시작일", "doc_id": doc_id, "source": r["source"],
                              "current_override": r.get("입찰 참여 시작일_dt"), "candidate_classification": "재검토 필요",
                              "candidate_snippet": contexts[0], "action_needed": "본문에 날짜가 있으니 추정치 대신 이 값이 맞는지 확인"})
    print(f"  (본문에 명시적 날짜가 없어 추정치를 그대로 써도 무난한 문서 {len(start_targets) - flagged}건은 생략)")

    # --- 4. 공고 번호 결측 (텍스트 검증 대상 아님) ---
    notice_targets = raw.loc[raw["공고 번호"].isna(), "파일명"].tolist()
    print()
    print("=" * 100)
    print(f"[4] 공고 번호 결측: {len(notice_targets)}건 - 행정 식별자라 본문에서 복구 불가(기존 결론), doc_id는 파일명 사용 중이라 조치 불필요")
    for doc_id in notice_targets:
        print(f"  - {doc_id}")
        rows_out.append({"category": "공고번호", "doc_id": doc_id, "source": None, "current_override": None,
                          "candidate_classification": None, "candidate_snippet": None, "action_needed": "조치 불필요(파일명을 doc_id로 사용 중)"})

    if rows_out:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
        print()
        print(f"전체 결과 저장: {OUT_PATH}")
        print("action_needed 컬럼이 '조치 불필요'/'N/A'가 아닌 행부터 우선 확인하면 돼.")


if __name__ == "__main__":
    main()
