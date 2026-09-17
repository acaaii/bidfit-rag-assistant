"""표 내용을 문서 끝에 몰아 붙이는 대신 원래 있던 자리에 끼워넣도록 바꾼
patch(src/parsers/hwp_parser.py, src/merge_text.py, 2026-08-28)를 실제
원본 문서로 검증하는 스크립트.

[배경] 예전엔 merge_text._flatten_tables()가 모든 표 내용을 문서 맨 끝에
일괄 첨부해서, "6. 추진일정" 같은 제목/문맥이 표 내용과 분리되는 문제가
있었다(한빈 지적, 2026-08-28). 고친 뒤에는 hwp5txt가 표 자리에 남기는
"<표>" 자리표시자 위치에 실제 표 내용을 직접 끼워넣는다 - 단, "<표>" 개수와
추출된 표 개수가 정확히 일치할 때만 시도하고, 하나라도 다르면 안전하게
예전 방식(문서 끝 일괄 첨부)으로 폴백한다.

이 스크립트는 캐시(output/merged_docs.pkl)를 쓰지 않고 원본 hwp 파일을
`src.data_processing.merge_text.merge_one()`으로 즉석 재파싱해서(캐시를 오염시키지 않음),
아래를 확인한다:
  1. "<표>" 자리표시자 개수와 실제 추출된 표 개수가 이 문서에서 일치하는지
  2. 일치한다면(inline 성공) - "6. 추진일정" 제목과 표 내용([표] 마커)이
     실제로 서로 가까이 붙어 있는지 눈으로 확인할 수 있게 그 주변 텍스트를
     출력
  3. 결과 전체를 output/inline_table_verification.txt로 저장

[참고] 반드시 한빈 로컬(파이참)에서 실행해야 함 - 공용 개발 환경엔
이 문서 원본 hwp 파일이 없다(data/files/ 비어있음).

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요). 문서 하나만
즉석으로 재파싱하는 거라 최대 90초 정도면 끝난다(전체 캐시 재파싱과는
무관 - output/merged_docs.pkl은 건드리지 않는다).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CSV_PATH, OUTPUT_DIR  # noqa: E402
from src.data_processing.merge_text import merge_one  # noqa: E402

import pandas as pd  # noqa: E402

DOC_FILE_NAME = "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp"
OUT_PATH = OUTPUT_DIR / "inline_table_verification.txt"


def main():
    df = pd.read_csv(CSV_PATH)
    row = df[df["파일명"] == DOC_FILE_NAME]
    if row.empty:
        print(f"'{DOC_FILE_NAME}'을 {CSV_PATH}에서 찾을 수 없습니다.")
        return
    r = row.iloc[0].copy()
    # merge_one()은 doc_id 컬럼이 있어야 함 - load_metadata의 doc_id 부여 규칙과
    # 동일하게(파일명을 doc_id로 사용) 맞춰준다.
    r["doc_id"] = r["파일명"]

    print(f"'{DOC_FILE_NAME}' 즉석 재파싱 중... (최대 90초)")
    merged = merge_one(r)

    print("=" * 100)
    print(f"source = {merged.source}  (raw_parsed여야 진짜 원본 재파싱)")
    print(f"n_tables = {merged.n_tables}")
    print(f"parse_note = {merged.parse_note}")
    print(f"본문 길이 = {len(merged.text)}자")
    print()

    note = merged.parse_note or ""
    # [수정] "inline" in note로만 체크하면 실패 메시지("inline 삽입 포기")에도
    # "inline"이라는 부분 문자열이 들어있어서 오판된다(2026-08-28, 실제 문서
    # 검증 중 발견) - 실제 성공/실패 문구에만 있는 고유한 부분 문자열로
    # 구분해야 한다. 폴백 여부를 먼저 확인하는 순서로도 바꿔서 이중 안전.
    if merged.source != "raw_parsed":
        print(f">> 원본 재파싱 자체가 안 됐습니다(source={merged.source!r}) - inline 삽입 여부를 판단할 수 없는 상태.")
        print("   (공용 개발 환경에서 이 메시지가 뜨는 건 정상 - 원본 파일이 없어서. 로컬에서 이게 뜨면 원본 파일 경로부터 확인해주세요.)")
    elif "삽입 포기" in note or "폴백" in note:
        print(">> inline 삽입 실패 -> 폴백(문서 끝 일괄 첨부)됨. 자리표시자/표 개수 불일치.")
    elif "끼워넣음" in note:
        print(">> inline 삽입 성공: 표 내용이 원래 위치에 끼워넣어졌습니다.")
    elif merged.n_tables == 0:
        print(">> 이 문서엔 애초에 표가 없는 것으로 추출됐습니다(n_tables=0).")
    else:
        print(">> 판단 불가 - parse_note 원문을 직접 확인해주세요.")

    # "6. 추진일정" 제목과 그 다음 표 마커([표] 또는 [표: 기간표(추진일정 등)로
    # 추정]) 사이 거리를 확인. Gantt형으로 인식됐다면 "작업명: M, M+1..." 같은
    # 활성 기간 텍스트가 그 뒤에 바로 이어져야 한다.
    text = merged.text
    idx_title = text.find("추진일정")
    idx_table_marker = text.find("[표", idx_title) if idx_title != -1 else -1
    if idx_table_marker != -1 and "기간표" in text[idx_table_marker:idx_table_marker + 60]:
        print(">> 표 마커가 '[표: 기간표(추진일정 등)로 추정]' 형태 - Gantt 표로 인식되어 활성 기간이 텍스트로 복원된 것으로 보임(아래 미리보기에서 '작업명: M, M+1...' 형태 확인).")

    print()
    if idx_title == -1:
        print("본문에서 '추진일정' 문구를 찾지 못했습니다 (표기가 다를 수 있음 - 아래 전체 텍스트에서 직접 확인해주세요).")
    elif idx_table_marker == -1:
        print("'추진일정' 제목은 찾았지만 그 뒤에서 '[표]' 마커를 찾지 못했습니다 - inline 삽입이 안 됐거나 이 표가 다른 형태로 처리됐을 수 있습니다.")
    else:
        gap = idx_table_marker - idx_title
        print(f"'추진일정' 제목 위치 {idx_title} -> 다음 '[표]' 마커까지 거리 {gap}자")
        if gap < 500:
            print("-> 제목과 표 내용이 가까이 붙어있음 (기대한 대로 문맥 유지된 것으로 보임).")
        else:
            print("-> 거리가 꽤 있음 - 둘 사이에 다른 섹션이 끼어있거나, 이 표가 다른 자리에 삽입됐을 수 있음. 아래 미리보기로 직접 확인 필요.")
        preview_start = max(0, idx_title - 50)
        preview_end = min(len(text), idx_table_marker + 800)
        print()
        print("--- '추진일정' 주변 미리보기 ---")
        print(text[preview_start:preview_end])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        f"source={merged.source}\nn_tables={merged.n_tables}\nparse_note={merged.parse_note}\n\n{text}",
        encoding="utf-8",
    )
    print()
    print("=" * 100)
    print(f"전체 결과 저장: {OUT_PATH}")
    print("이 파일 전체(또는 위 콘솔 출력)를 참고해서 다음 단계(전체 재파싱 여부 등)를 판단하자.")


if __name__ == "__main__":
    main()
