"""표 위치 복원(inline 삽입)/Gantt형 표 파싱이 국방과학연구소 문서 하나만의
이슈였는지, 코퍼스 전체에 걸쳐 있는 문제인지 통계를 낸다.

[배경] 12절(rfp-data-preprocessing-plan.md)에서 만든 패치는:
  1. "<표>" 자리표시자 위치에 표 내용을 원래 자리에 끼워넣기(inline) - 자리
     표시자 개수와 표 개수가 정확히 일치할 때만 시도하고, 안 맞으면 문서 끝
     일괄 첨부로 폴백.
  2. "M"/"M+1"/... 헤더가 있는 Gantt형 표는 셀 배경색으로 활성 기간을
     읽어서 텍스트로 복원.

국방과학연구소 문서 하나로는 검증됐지만(표 109/109 일치, inline 성공,
Gantt표 정상 복원), 이게 이 문서만의 특수 케이스인지 다른 문서들도 비슷한
빈도로 걸리는지는 아직 모른다. 이 스크립트는 **재파싱을 다시 하지 않고**
이미 한빈이 방금 전체 재실행해서 만든 output/merged_docs.pkl 캐시를 그대로
불러와, merge_one()이 각 문서 처리 시 남긴 parse_note를 분석해서 통계를
낸다(캐시에 이미 inline 성공/폴백 여부, 자리표시자/표 개수가 다 기록돼있음
- merge_text.py의 accept_note 로직 참고).

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요). 재파싱 없이 캐시만
읽으므로 몇 초면 끝난다.
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR  # noqa: E402
from src.data_processing.merge_text import load_merged  # noqa: E402

OUT_PATH = OUTPUT_DIR / "table_handling_stats.csv"

_MISMATCH_RE = re.compile(r"자리표시자 개수\((\d+)\)와 추출된 표 개수\((\d+)\) 불일치")


def main():
    df = load_merged()
    if df is None:
        print("output/merged_docs.pkl 캐시가 없습니다 - step2_merge_text.py를 먼저 실행해주세요.")
        return

    with_tables = df[df["n_tables"] > 0].copy()
    print(f"전체 문서 {len(df)}건 중 표가 1개 이상 추출된 문서: {len(with_tables)}건")
    print()

    rows_out = []
    n_inline_ok = 0
    n_fallback = 0
    n_gantt_docs = 0
    n_gantt_tables_total = 0
    mismatch_details = []

    for _, r in with_tables.iterrows():
        note = r.get("parse_note") or ""
        text = r["text"] if isinstance(r["text"], str) else ""
        gantt_count = text.count("[표: 기간표(추진일정 등)로 추정]")
        if gantt_count:
            n_gantt_docs += 1
            n_gantt_tables_total += gantt_count

        status = "판단불가"
        placeholder_n = table_n = None
        m = _MISMATCH_RE.search(note)
        if "끼워넣음" in note:
            status = "inline 성공"
            n_inline_ok += 1
        elif m:
            status = "폴백(개수 불일치)"
            n_fallback += 1
            placeholder_n, table_n = int(m.group(1)), int(m.group(2))
            mismatch_details.append((r["doc_id"], placeholder_n, table_n, r["n_tables"]))
        elif "폴백" in note:
            status = "폴백(기타)"
            n_fallback += 1

        rows_out.append({
            "doc_id": r["doc_id"],
            "n_tables": r["n_tables"],
            "status": status,
            "gantt_tables": gantt_count,
            "placeholder_count": placeholder_n,
            "top_level_table_count": table_n,
        })

    print(f"inline 삽입 성공: {n_inline_ok}건")
    print(f"폴백(문서 끝 일괄 첨부): {n_fallback}건")
    print()

    if mismatch_details:
        print("=" * 100)
        print(f"자리표시자/표 개수 불일치로 폴백된 문서 {len(mismatch_details)}건 상세:")
        for doc_id, ph, tb, n_tables in mismatch_details:
            print(f"  {doc_id}")
            print(f"    자리표시자 {ph}개 vs 최상위 표 {tb}개 (차이 {abs(ph - tb)}, n_tables={n_tables})")
    else:
        print("자리표시자/표 개수 불일치로 폴백된 문서 없음 - 국방과학연구소 문서가 예외적인 케이스였을 수도, 아니면 이번엔 안 걸렸을 수도 있음(원인이 주로 '중첩 표'인데, 중첩 표 자체가 흔치 않을 수 있음).")

    print()
    print("=" * 100)
    print(f"Gantt형(기간표) 표가 감지된 문서: {n_gantt_docs}건 (표 총 {n_gantt_tables_total}개)")
    if n_gantt_docs:
        gantt_docs = with_tables[with_tables["text"].str.contains(
            re.escape("[표: 기간표(추진일정 등)로 추정]"), regex=True, na=False)]
        for doc_id in gantt_docs["doc_id"]:
            cnt = df.loc[df["doc_id"] == doc_id, "text"].iloc[0].count("[표: 기간표(추진일정 등)로 추정]")
            print(f"  - {doc_id} (표 {cnt}개)")

    if rows_out:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
        print()
        print(f"전체 결과 저장: {OUT_PATH}")

    print()
    print("=" * 100)
    print("해석 가이드:")
    print("- 폴백 건수가 많으면: 중첩 표가 흔한 코퍼스라는 뜻 - _top_level_tables()류 처리를 더 일반화할 가치가 있음.")
    print("- Gantt형 표가 여러 문서에 있으면: 이번 배경색 파싱 패치의 영향 범위가 국방과학연구소 문서 하나가 아니라는 뜻 -")
    print("  다른 Gantt형 표 몇 개를 더 표본으로 뽑아 (기간)/(시점) 텍스트가 실제로도 말이 되는지 눈으로 확인해볼 가치가 있음.")


if __name__ == "__main__":
    main()
