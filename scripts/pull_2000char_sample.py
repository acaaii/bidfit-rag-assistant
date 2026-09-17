"""국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp 한 문서에서
팀원이 보내준 것과 비교할 수 있게 텍스트 샘플을 뽑는 스크립트.

[2026-08-28] 팀원이 자기 쪽 처리 결과에서 2000자를 뽑아 보내줬다고 해서,
우리 쪽도 같은 문서로 비교 가능한 샘플을 뽑는다. 팀원이 정확히 어느
지점(문서 맨 앞부터인지, 특정 표 부분인지)에서 2000자를 잘랐는지 모르므로
아래 두 가지를 다 뽑아서 한빈이 눈으로 맞춰볼 수 있게 한다:

  1. 본문 맨 앞 2000자 (chunking 직전 clean_text_for_chunking() 적용 후) -
     팀원이 그냥 처음부터 잘랐을 가능성에 대비
  2. "[표]" 마커(우리 쪽 _flatten_tables()가 표 내용을 복원해 붙이는 자리 -
     팀원이 지운다는 "<표>" 자리표시자와는 다른 대괄호 표기) 주변 2000자씩,
     찾은 표 블록마다 - 팀원이 표가 있던 부분에서 잘랐을 가능성에 대비

결과는 콘솔 + output/sample_2000char_comparison.txt로 저장.

[참고] 반드시 한빈 로컬(파이참)에서 실행해야 함 - 공용 개발 환경에서는
이 문서가 source='csv_fallback', n_tables=0이라(원본 파일이 없어서) 표
내용이 애초에 없다. 로컬 재파싱 캐시 기준으로 돌려야 의미 있음.

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_processing.chunking import clean_text_for_chunking  # noqa: E402
from src.config import OUTPUT_DIR  # noqa: E402
from src.data_processing.merge_text import load_merged  # noqa: E402

DOC_ID = "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp"
SAMPLE_LEN = 2000
OUT_PATH = OUTPUT_DIR / "sample_2000char_comparison.txt"


def main():
    df = load_merged()
    if df is None:
        print("output/merged_docs.pkl 캐시가 없습니다 - step2/step3을 먼저 실행해주세요.")
        return

    row = df[df["doc_id"] == DOC_ID]
    if row.empty:
        print(f"'{DOC_ID}' 문서를 merged_docs.pkl에서 찾을 수 없습니다.")
        print("후보:", [d for d in df["doc_id"] if "국방과학연구소" in d])
        return

    r = row.iloc[0]
    raw_text = r["text"] if isinstance(r["text"], str) else ""
    text = clean_text_for_chunking(raw_text)

    print("=" * 100)
    print(f"[{DOC_ID}]")
    print(f"  source(재파싱 여부) = {r['source']!r}  (raw_parsed여야 진짜 원본 재파싱)")
    print(f"  n_tables = {r.get('n_tables')}   doc_type = {r.get('doc_type')}")
    print(f"  본문 길이(clean 후) = {len(text)}자")
    if r["source"] != "raw_parsed":
        print("  !! source가 raw_parsed가 아닙니다 - 원본 파일 경로/재파싱 캐시부터 확인하세요. 아래 샘플은 신뢰할 수 없습니다.")
    print()

    sections = []

    # 1) 맨 앞 2000자
    head = text[:SAMPLE_LEN]
    sections.append(("맨 앞 2000자", head))

    # 2) "[표]" 마커 주변 2000자씩 (마커 시작 지점부터)
    table_positions = []
    start = 0
    while True:
        idx = text.find("[표]", start)
        if idx == -1:
            break
        table_positions.append(idx)
        start = idx + 1

    print(f"  '[표]' 마커 발견 개수: {len(table_positions)}개 (텍스트 내 위치: {table_positions[:10]}{'...' if len(table_positions) > 10 else ''})")
    print()

    # 마커가 너무 촘촘히 붙어있으면 대표로 몇 개만(200자 이상 떨어진 것들) 뽑는다
    picked = []
    for pos in table_positions:
        if not picked or pos - picked[-1] > 200:
            picked.append(pos)
    picked = picked[:3]  # 최대 3구간

    for i, pos in enumerate(picked):
        s = max(0, pos - 100)  # 표 시작 조금 전부터
        e = min(len(text), s + SAMPLE_LEN)
        sections.append((f"[표] 마커 #{i+1} 주변 2000자 (텍스트 내 위치 {pos})", text[s:e]))

    lines_out = [
        f"문서: {DOC_ID}",
        f"source: {r['source']}  n_tables: {r.get('n_tables')}  본문 길이: {len(text)}자",
        "",
    ]
    for title, sample in sections:
        header = f"----- {title} (길이={len(sample)}자) -----"
        print(header)
        print(sample)
        print()
        lines_out.append(header)
        lines_out.append(sample)
        lines_out.append("")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines_out), encoding="utf-8")
    print("=" * 100)
    print(f"저장됨: {OUT_PATH}")
    print("팀원이 보내준 2000자와 겹치는 구간을 찾아서(같은 문장/제목이 보이는 부분) 나란히 놓고 비교하면 됨.")
    print("'[표]' 마커 주변 샘플에 항목/값이 실제로 채워져 있는데 팀원 것엔 그 부분이 비어있거나 통째로 없다면 가설이 맞는 것.")


if __name__ == "__main__":
    main()
