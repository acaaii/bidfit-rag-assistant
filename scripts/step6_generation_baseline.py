"""7단계(신규): Generation Baseline - Retrieval 결과를 근거로 LLM이 실제 답을
생성하게 하고, golden set의 gold_answer/required_facts로 채점한다.

[배경] 멘토링 노트가 강조한 대로 Retrieval 평가("정답 chunk를 찾았는지")와
Answer 평가("최종 답변이 실제로 맞는지")는 분리해서 봐야 하는 지표다
(step5_evaluate.py는 전자만 다룸). 이 스크립트는 후자의 첫 baseline이다 -
아직 팀 논의 전이라 우선 혼자 돌려보는 용도로, 복잡한 LLM 채점 대신 가장
단순한 방식(멘토링 노트: "처음엔 Pass/Fail 이진 스케일")으로 시작한다:
golden_testset_verified_111_v6.json에 이미 있는 required_facts(질문마다
"답에 반드시 포함되어야 할 사실" 목록, 사실 하나당 인정 가능한 표현들의
리스트)가 생성된 답변 문자열에 실제로 들어있는지 단순 포함 여부로 검사한다.
LLM 채점 없이 문자열 매칭만 쓰므로 채점용 LLM을 생성용과 분리해야 한다는
멘토링 노트 원칙도 자연히 지켜진다(애초에 LLM으로 채점하지 않음).

Retrieval 부분은 새로 만들지 않고 지금까지 검증한 HybridIndex.hybrid_search()
를 그대로 재사용한다(retrieval 품질 자체는 계속 한빈이 별도로 개선 중).
Generation 호출/채점 공통 로직(gpt-5-mini 호출, required_facts 채점)은
src/generation.py로 옮겼다 - golden-set-v3-share용 step8도 같은 로직을
재사용해서 중복을 없앴다(2026-09-01).

사전 준비:
    - `pip install openai` (requirements.txt에 추가해둠)
    - 환경변수 OPENAI_API_KEY 설정 (터미널에 export OPENAI_API_KEY=... 또는
      PyCharm Run Configuration의 Environment variables에 추가)

사용법: python scripts/step6_generation_baseline.py
    - 전체 111개를 다 돌리면 API 비용이 드니, 처음 돌려볼 땐 아래 LIMIT을
      작은 수(예: 5)로 바꿔서 몇 개만 먼저 확인해보는 걸 권장.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.data_processing.chunking import chunk_all, load_chunks, save_chunks  # noqa: E402
from src.config import OUTPUT_DIR  # noqa: E402
from src.evaluation.evaluation import load_golden_set  # noqa: E402
from src.generation.generation import build_context, check_required_facts, generate_answer  # noqa: E402
from src.retrieval.indexing import HybridIndex  # noqa: E402
from src.data_processing.load_metadata import load_clean_metadata  # noqa: E402
from src.data_processing.merge_text import load_merged, merge_all, save_merged  # noqa: E402

TOP_K = 5  # step5_evaluate.py의 recall@5와 비교 가능하도록 동일하게 맞춤
LIMIT = None  # 테스트 삼아 일부만 돌려보고 싶으면 정수로 바꾸기 (예: 5)
GEN_RESULTS_PATH = OUTPUT_DIR / "generation_eval.csv"


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "! 환경변수 OPENAI_API_KEY가 설정되어 있지 않습니다. "
            "터미널에서 export OPENAI_API_KEY=sk-... 로 설정하거나 "
            "PyCharm Run Configuration의 Environment variables에 추가한 뒤 다시 실행하세요."
        )
        return

    from openai import OpenAI

    client = OpenAI()

    golden_df = load_golden_set()

    if "answerability" in golden_df.columns:
        n_before = len(golden_df)
        golden_df = golden_df[golden_df["answerability"].fillna("answerable") == "answerable"].reset_index(drop=True)
        n_skipped = n_before - len(golden_df)
        if n_skipped:
            print(f"[step6] answerability != 'answerable'인 질문 {n_skipped}건은 이번 baseline에서 제외(응답 거부 평가는 별도 과제)")

    has_grading_fields = "gold_answer" in golden_df.columns and "required_facts" in golden_df.columns
    if not has_grading_fields:
        print(
            "[step6] golden set에 gold_answer/required_facts 컬럼이 없어서 "
            "생성된 답변만 저장하고 자동 채점(Pass/Fail)은 건너뜁니다 "
            "(golden_testset_verified_111_v6.json이 아니라 구버전 golden_set.json을 쓰고 있는 경우 정상)."
        )

    if LIMIT:
        golden_df = golden_df.head(LIMIT)
        print(f"[step6] LIMIT={LIMIT} - 앞 {LIMIT}개 질문만 실행")

    chunks = load_chunks()
    if chunks is None:
        print("[step6] 캐시(output/chunks.pkl) 없음 -> step2~3부터 다시 계산합니다...")
        df = load_merged()
        if df is None:
            df = merge_all(load_clean_metadata())
            save_merged(df)
            df = load_merged()
        chunks = chunk_all(df)
        save_chunks(chunks)

    index = HybridIndex(chunks)
    print(f"인덱싱 완료: chunk {len(chunks)}개, 임베딩 백엔드={index.embedding_backend.name}\n")

    rows = []
    n = len(golden_df)
    for i, (_, row) in enumerate(golden_df.iterrows(), start=1):
        query = row["query"]
        print(f"[{i}/{n}] {query[:50]}...")

        hits = index.hybrid_search(query, k=TOP_K)
        context = build_context(hits)
        retrieved_doc_ids = [h.doc_id for h in hits]
        expected = set(row["expected_doc_id"]) if isinstance(row.get("expected_doc_id"), list) else set()
        retrieval_hit = bool(expected) and any(d in expected for d in retrieved_doc_ids)

        answer = generate_answer(client, query, context)

        record = {
            "id": row.get("id"),
            "difficulty": row.get("난이도"),
            "query": query,
            "generated_answer": answer,
            "retrieved_doc_ids": " | ".join(retrieved_doc_ids),
            "retrieval_hit": retrieval_hit,
        }

        if has_grading_fields:
            gold_answer = row.get("gold_answer")
            required_facts = row.get("required_facts")
            record["gold_answer"] = gold_answer
            if answer is not None:
                matched, total = check_required_facts(answer, required_facts)
                record["facts_matched"] = matched
                record["facts_total"] = total
                record["fact_coverage"] = (matched / total) if total else None
                record["pass"] = (matched == total) if total else None
            else:
                record["facts_matched"] = None
                record["facts_total"] = None
                record["fact_coverage"] = None
                record["pass"] = False  # 생성 자체가 실패했으면 채점상 Fail로 기록

        rows.append(record)
        time.sleep(0.2)  # 과도한 연속 호출로 인한 rate limit 방지용 소폭 지연

    result_df = pd.DataFrame(rows)
    result_df.to_csv(GEN_RESULTS_PATH, index=False, encoding="utf-8-sig")
    print(f"\n질의별 상세 결과 저장: {GEN_RESULTS_PATH}")

    n_errors = int(result_df["generated_answer"].isna().sum())
    if n_errors:
        print(f"생성 실패(API 에러 등) {n_errors}건 - 콘솔 로그에서 에러 메시지 확인")

    if has_grading_fields:
        gradable = result_df[result_df["facts_total"] > 0]
        if len(gradable) > 0:
            pass_rate = gradable["pass"].mean()
            avg_coverage = gradable["fact_coverage"].mean()
            print(f"\n=== Generation Baseline 요약 (채점 가능한 {len(gradable)}건 기준) ===")
            print(f"Pass율(모든 required_facts 다 맞춘 비율): {pass_rate:.3f}")
            print(f"평균 fact coverage: {avg_coverage:.3f}")

            retrieval_hit_rate = result_df["retrieval_hit"].mean()
            print(f"\n참고: 이번 배치의 hybrid retrieval_hit 비율(top-{TOP_K} 안에 정답 문서 포함) = {retrieval_hit_rate:.3f}")
            print("-> Retrieval은 맞았는데 Generation이 틀린 케이스와, Retrieval부터 틀린 케이스를 구분하려면")
            print("   generation_eval.csv에서 retrieval_hit=True인데 pass=False인 행들을 직접 확인하세요.")


if __name__ == "__main__":
    main()
