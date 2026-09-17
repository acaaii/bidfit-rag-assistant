"""14단계(신규): step13이 이미 저장해 둔 answer 텍스트(output/
v3_answer56_generation_compare.csv)를 재사용해서 "채점 로직만" 다시 돌린다.
gpt-5-mini를 다시 호출하지 않는다(비용/시간 재발생 없음) - 한빈이 명시적으로
"채점만 다시 해 보자. 생성을 다시 하진 말고"라고 요청한 것을 그대로 반영.

[배경 - 2026-09-02 밤] step13 결과 CSV를 직접 열어 11건의
abstention_match 불일치와 다수의 citation_coverage=0 사례를 전수 조사한
결과, 시스템 품질 문제가 아니라 채점 코드 자체의 버그 두 개였다는 걸
확인했다(src/generation.py에 수정 완료):

  1. is_abstention() - "ABSTENTION_PHRASE가 답변 어디에든 있으면 기권"으로
     판정했는데, 다중 답변(하위 질문 여러 개짜리 문항)에서 그중 "일부만"
     모른다고 답한 경우까지 "완전 기권"으로 오분류했다(9건: c12/c13/c19/
     c25/g12/g13/g14/g19/g23). 반대로 진짜 완전 기권(g22/g24, decision==
     "abstain")인데 모델이 프롬프트 지시 문구를 그대로 안 쓰고 표현을 바꿔
     답한 경우는 놓쳤다. -> 문구를 뺀 "나머지"에 실질 내용이 남는지로
     판단하도록 수정.
  2. extract_cited_doc_ids() - "[근거: ...]" 안의 첫 "]"에서 멈추는
     정규식이라, 인용된 RFP 파일명 자체에 "[재공고][긴급]" 같은 대괄호
     태그가 들어있으면 거기서 잘렸다(5건: c09/c11/c16/g06/g15) -> 문자열
     끝까지 통째로 캡처하도록 수정.

[다중 답변 부분점수 관련 - 한빈 질문에 대한 답] abstention_behavior_match
자체는 여전히 이진(맞음/틀림)으로 둔다. 부분적으로만 아는 경우의 "정도"는
이미 별도 지표(citation_coverage/fact_coverage, 둘 다 0~1 비율)가 재고
있어서, 여기에 또 부분점수 축을 추가하면 같은 걸 두 번 재는 셈이고
팀원의 이진 설계(뭐가 됐든 True/False)와도 비교가 안 맞게 된다. 진짜
문제는 "점수 방식"이 아니라 "완전 기권 vs 일부만 모름을 잘못 구분하는
판정 버그"였다 - 그래서 점수 체계는 그대로 두고 판정 함수만 고쳤다.

이 스크립트가 하는 일: CSV의 answer 컬럼(이미 생성된 텍스트)과
golden_set_v3의 required_doc_id/required_fact_groups/decision을 다시
합쳐서, 고친 is_abstention/extract_cited_doc_ids로 abstention_match /
citation_coverage / fact_coverage를 재계산하고, "고치기 전(CSV에 이미
있던 값)" vs "고친 후" vs "팀원" 세 열로 비교한다.

사용법: python scripts/step14_v3_generation_rescore.py
(사전 조건: step13을 먼저 돌려서 output/v3_answer56_generation_compare.csv가
있어야 함 - 없으면 에러 메시지와 함께 안내)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.config import OUTPUT_DIR  # noqa: E402
from src.generation.generation import (  # noqa: E402
    check_required_facts,
    compute_abstention_match,
    compute_citation_coverage,
    is_abstention,
)
from src.evaluation.golden_set_v3 import load_golden_set_v3  # noqa: E402

PREV_RESULTS_PATH = OUTPUT_DIR / "v3_answer56_generation_compare.csv"
RESCORED_PATH = OUTPUT_DIR / "v3_answer56_generation_rescore.csv"

TEAMMATE = {
    "abstention_behavior_match": 0.732143,
    "required_doc_citation_coverage": 0.472222,
    "lexical_required_fact_coverage": 0.280769,
}


def main():
    if not PREV_RESULTS_PATH.exists():
        print(
            f"[step14] {PREV_RESULTS_PATH}가 없습니다. 먼저 "
            f"step13_v3_generation_compare.py를 돌려서 answer를 생성/저장해야 합니다 "
            f"(이 스크립트는 재생성을 하지 않고 그 결과만 재사용합니다)."
        )
        sys.exit(1)

    prev = pd.read_csv(PREV_RESULTS_PATH, encoding="utf-8-sig")
    print(f"[step14] 이전 결과(이미 생성된 answer) 로드: {len(prev)}건, 재생성 없이 채점만 다시 함")

    df = load_golden_set_v3()
    rag56 = df[df["source_lane"] == "answer"].reset_index(drop=True)
    meta_by_id = {row["id"]: row for _, row in rag56.iterrows()}

    rows = []
    for _, prow in prev.iterrows():
        case_id = prow["id"]
        meta = meta_by_id.get(case_id)
        if meta is None:
            print(f"  ! 경고: {case_id}를 golden_set_v3에서 못 찾음 - 건너뜀")
            continue

        # NaN(빈 answer, 즉 생성 실패였던 케이스)을 None으로 정규화.
        answer = prow["answer"] if isinstance(prow["answer"], str) else None
        expected_should_abstain = bool(meta["decision"] == "abstain")
        required_doc_ids = meta["expected_doc_id"] if isinstance(meta["expected_doc_id"], list) else []
        required_facts = meta.get("required_fact_groups")

        # citation coverage: step13과 동일한 규칙(기권 기대 문항은 분모 자체를
        # 0으로 둬서 채점 대상에서 뺌 - 팀원 metric_coverage.citation=54와 맞춤)
        if expected_should_abstain:
            citation_matched, citation_total = 0, 0
        elif answer is None:
            citation_matched, citation_total = 0, len(required_doc_ids)
        else:
            citation_matched, citation_total = compute_citation_coverage(answer, required_doc_ids)  # 고친 정규식 사용

        # fact coverage: 실제 답변이 (고친 판정 기준으로) 기권이면 분모 0
        if answer is None or is_abstention(answer):  # 고친 is_abstention 사용
            fact_matched, fact_total = 0, 0
        else:
            fact_matched, fact_total = check_required_facts(answer, required_facts)

        abstention_match = compute_abstention_match(answer, expected_should_abstain)  # 고친 is_abstention 사용

        rows.append({
            "id": case_id,
            "decision": meta["decision"],
            "expected_should_abstain": expected_should_abstain,
            "abstention_match_old": prow["abstention_match"],
            "abstention_match_new": abstention_match,
            "citation_coverage_old": prow["citation_coverage"],
            "citation_total_new": citation_total,
            "citation_coverage_new": (citation_matched / citation_total) if citation_total else None,
            "fact_coverage_old": prow["fact_coverage"],
            "fact_total_new": fact_total,
            "fact_coverage_new": (fact_matched / fact_total) if fact_total else None,
        })

    result_df = pd.DataFrame(rows)
    result_df.to_csv(RESCORED_PATH, index=False, encoding="utf-8-sig")
    print(f"\n상세 재채점 결과 저장: {RESCORED_PATH}")

    # 바뀐 케이스만 골라서 보여주기
    changed_abstention = result_df[result_df["abstention_match_old"] != result_df["abstention_match_new"]]
    print(f"\n[step14] abstention_match가 바뀐 케이스: {len(changed_abstention)}건")
    if len(changed_abstention):
        print(changed_abstention[["id", "decision", "abstention_match_old", "abstention_match_new"]]
              .to_string(index=False))

    new_ours = {
        "abstention_behavior_match": result_df["abstention_match_new"].mean(),
        "required_doc_citation_coverage": result_df.loc[
            result_df["citation_total_new"] > 0, "citation_coverage_new"
        ].mean(),
        "lexical_required_fact_coverage": result_df.loc[
            result_df["fact_total_new"] > 0, "fact_coverage_new"
        ].mean(),
    }
    old_ours = {
        "abstention_behavior_match": result_df["abstention_match_old"].mean(),
        "required_doc_citation_coverage": prev.loc[prev["citation_total"] > 0, "citation_coverage"].mean(),
        "lexical_required_fact_coverage": prev.loc[prev["fact_total"] > 0, "fact_coverage"].mean(),
    }
    n_citation_new = int((result_df["citation_total_new"] > 0).sum())
    n_fact_new = int((result_df["fact_total_new"] > 0).sum())

    print(f"\n{'=' * 78}\n채점 버그 수정 전/후 vs 팀원 (재생성 없음 - 같은 answer 텍스트, 채점 로직만 다름)\n{'=' * 78}")
    print(f"{'지표':<32}{'수정전':>10}{'수정후':>10}{'(N)':>5}{'팀원':>10}{'수정후-팀원':>12}")
    for key, label in [
        ("abstention_behavior_match", "abstention_behavior_match"),
        ("required_doc_citation_coverage", "required_doc_citation_coverage"),
        ("lexical_required_fact_coverage", "lexical_required_fact_coverage(참고용)"),
    ]:
        n = len(result_df) if key == "abstention_behavior_match" else (
            n_citation_new if "citation" in key else n_fact_new
        )
        print(
            f"{label:<32}{old_ours[key]:>10.3f}{new_ours[key]:>10.3f}{n:>5}"
            f"{TEAMMATE[key]:>10.3f}{new_ours[key] - TEAMMATE[key]:>+12.3f}"
        )

    print(
        "\n주의: 위 '수정후' 수치는 gpt-5-mini를 다시 호출하지 않고, step13이 이미 저장한 "
        "answer 텍스트를 고친 채점 함수(is_abstention/extract_cited_doc_ids)로만 다시 잰 것이다 - "
        "즉 모델 답변 자체는 완전히 동일하고 '우리가 그걸 어떻게 채점하느냐'만 바뀐 결과."
    )


if __name__ == "__main__":
    main()
