"""팀원과 chunk 비교용 샘플을 뽑는 스크립트.

[2026-08-28] 팀원 파이프라인은 <표>/<그림> 태그만 지우고 표 내용 자체는
복구하지 않는다고 확인됐다("단일 구조고 태그만 지우고 끝이래"). 우리 쪽은
_flatten_tables()로 표 셀 내용을 실제로 복원해서 본문 뒤에 "[표]" 블록으로
붙인다(merge_text.py 참고, <표> 자리표시자와는 다른 대괄호 표기라 구분됨).

이 가설을 검증하려면 같은 문서, 같은 표 부분의 chunk를 양쪽에서 직접
눈으로 비교해야 한다. 이 스크립트는 그 비교용 샘플을 뽑는다:
  1. output/chunks.pkl(캐시 있으면 그걸, 없으면 output/merged_docs.pkl에서
     즉석으로 chunk_document() 호출)에서 지정한 문서의 chunk만 필터링
  2. "[표]" 마커가 포함된(=표 내용이 실제로 들어간) chunk를 우선순위로 골라
     몇 개만 출력 + output/sample_chunks_for_comparison.txt로 저장

[중요 - 참고] 이 스크립트는 반드시 한빈 로컬(파이참)에서 실행해야 함.
공용 개발 환경의 output/merged_docs.pkl은 원본 파일이 아예 없어서
(data/files/ 비어있음) 이 문서 둘 다 source='csv_fallback', n_tables=0으로
나온다 - 즉 공용 환경 쪽에는 표 내용이 애초에 없어서 비교 샘플을 대신 뽑아줄
수 없다. 반드시 재파싱이 끝난 한빈 로컬 캐시 기준으로 돌려야 의미 있는
비교가 된다.

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_processing.chunking import chunk_document, load_chunks  # noqa: E402
from src.config import OUTPUT_DIR  # noqa: E402
from src.data_processing.merge_text import load_merged  # noqa: E402

# 팀원이 표가 있다고 확인해 줄 만한, 우리 쪽에서도 표 내용이 실제로 복원된
# 문서 후보 2건. 둘 다 시도해서 [표] 마커가 있는 쪽으로 진행하면 된다.
CANDIDATE_DOC_IDS = [
    "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp",
    "국방과학연구소_대용량 자료전송시스템 고도화.hwp",
]

OUT_PATH = OUTPUT_DIR / "sample_chunks_for_comparison.txt"
MAX_CHUNKS_TO_SHOW = 4


def _get_chunks_for_doc(doc_id: str):
    cached = load_chunks()
    if cached is not None:
        return [c for c in cached if c.doc_id == doc_id]
    # 캐시 없으면 merged_docs.pkl에서 그 문서 하나만 즉석으로 chunk
    df = load_merged()
    if df is None:
        return None
    row = df[df["doc_id"] == doc_id]
    if row.empty:
        return []
    return chunk_document(row.iloc[0])


def main():
    lines_out = []
    for doc_id in CANDIDATE_DOC_IDS:
        chunks = _get_chunks_for_doc(doc_id)
        print("=" * 100)
        print(f"[{doc_id}]")
        if chunks is None:
            print("  output/merged_docs.pkl 캐시가 없습니다 - step2/step3을 먼저 실행해주세요.")
            continue
        if not chunks:
            print("  -> chunk가 0개(문서가 없거나 본문이 비어있음)")
            continue

        table_chunks = [c for c in chunks if "[표]" in c.text]
        other_chunks = [c for c in chunks if "[표]" not in c.text]
        print(f"  전체 chunk {len(chunks)}개 (strategy 분포: "
              f"{ {s: sum(1 for c in chunks if c.strategy == s) for s in ('parent', 'child', 'recursive')} })")
        print(f"  '[표]' 마커 포함 chunk: {len(table_chunks)}개")

        pick = table_chunks[:MAX_CHUNKS_TO_SHOW] or other_chunks[:MAX_CHUNKS_TO_SHOW]
        if not table_chunks:
            print("  -> 이 문서엔 표 내용이 복원된 chunk가 없음(n_tables=0이었을 수 있음) - 다른 후보로 비교 추천")

        for c in pick:
            header = f"--- chunk_id={c.chunk_id}  strategy={c.strategy}  길이={len(c.text)}자 ---"
            print(header)
            print(c.text[:500])
            print()
            lines_out.append(header)
            lines_out.append(c.text)
            lines_out.append("")

    if lines_out:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text("\n".join(lines_out), encoding="utf-8")
        print("=" * 100)
        print(f"저장됨: {OUT_PATH}")
        print("이 중 '[표]' 마커가 있는 chunk 몇 개를 팀원한테 보내면서, 팀원 쪽 같은 문서/같은 표 부분 chunk를")
        print("요청해서 나란히 비교하면 됨 - 팀원 것에는 표 내용(항목/값)이 아예 없고 <표> 자리도 사라져 있을 가능성이 큼.")


if __name__ == "__main__":
    main()
