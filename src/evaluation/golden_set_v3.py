"""golden-set-v3-share 패키지(팀원이 2026-09-01에 공유한 3차 API 기준선/보조
골든셋 묶음)를 우리 파이프라인이 쓸 수 있는 형태로 불러온다.

[배경] 팀원이 Downloads/golden-set-v3-share 폴더를 공유했다. 패키지 자체는
"최종 평가 자산 131건"(기존 유지 111 + 신규 20)을 표방하지만, 실제로 우리
코퍼스(output/merged_docs.pkl)와 하나씩 대조해보니 그대로 다 쓸 수는
없었다:

1. **lane(문항 그룹)마다 우리 코퍼스와 매칭 가능한 정보량이 다르다.**
   - `rag-56`(답변형 56건) / `set-13`(집합검색형 13건): `source_labels`
     필드에 사람이 읽을 수 있는 원본 파일명이 있어서 우리 코퍼스 doc_id와
     매칭 가능. 단 공백 2칸 vs 1칸 차이가 있는 파일명이 몇 개 있어서
     공백을 정규화해야 정확히 일치한다(예: "그랜드코리아레저(주)_2024년도
     GKL  그룹웨어..." 처럼 우리 코퍼스 쪽에 공백이 2칸인 경우가 있음).
   - `visual`(표/그림 10건): `document.source_filename`에 파일명이 있지만
     전부 "refined_" 접두어가 붙어있다. 접두어를 떼면 우리 코퍼스 doc_id와
     정확히 일치.
   - **`core40`(기존 핵심 40건)과 `corpus_analytics`(전체 통계 10건, 총
     50건)는 이 모듈이 읽지 않는다.** doc_id가 "doc_e3b910313338c8c5232ec2de"
     같은 내부 해시/ID뿐이고, 사람이 읽을 수 있는 파일명이나 우리 코퍼스에
     대조할 매핑 매니페스트가 이번 공유 패키지에 없었다(README가 "raw RFP
     source files"는 제외했다고 밝혔는데 이 doc_id->파일명 매핑 파일도 같이
     빠진 것으로 보임) - 팀원한테 매핑 매니페스트를 받기 전까지는 채점
     불가능. corpus_analytics는 애초에 "코퍼스 전체 통계"(예: "HWP/PDF가
     각각 몇 건") 질문이라 개별 문서 retrieval 평가 프레임과 맞지도 않는다
     (정답이 refined_data_list.csv 기준 집계값이라 완전히 다른 채점 로직이
     필요함 - 이번엔 손대지 않음).

   그래서 이 모듈이 실제로 반환하는 건 패키지가 말하는 "131건"이 아니라
   `rag-56 + set-13 + visual-10 = 79건`이다.

2. **패키지 자체가 "아직 켜지 않았다"고 데이터로 명시하고 있다.**
   `manifest.json`/`golden-set-count-contract.md`/`third-golden-set-index.json`
   세 문서 모두 "사람 승인 완료 0/131"이라고 밝히고 있고, 실제 JSONL
   레코드를 열어보면 rag-56/set-13 전 항목이 `"enabled": false`,
   `"review": {"status": "draft"}`로 박혀 있다(문서상 문구가 아니라 데이터
   자체가 그렇다). 그럼에도 지금 단계에서는 그대로 평가에 포함하기로
   했다(한빈 판단) - 이 모듈은 그 항목들을 걸러내지 않고 전부 반환하되,
   `v3_enabled`/`v3_review_status` 컬럼은 남겨서 나중에 언제든 걸러볼 수
   있게 한다.

사용법:
    아래 GOLDEN_SET_V3_DIR(기본 data/golden_set_v3/) 밑에 패키지에서
    아래 3개 파일만 그대로 복사해서 넣으면 된다(나머지 파일은 필요 없음):
        - evaluation/private/supplemental/build-v1/rag-56.draft.jsonl
        - evaluation/private/supplemental/build-v1/set-13.draft.jsonl
        - golden-set-final/document-structure-visual-qa.jsonl
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from ..config import DATA_DIR

GOLDEN_SET_V3_DIR = DATA_DIR / "golden_set_v3"

_FILES = {
    "answer": "rag-56.draft.jsonl",
    "set": "set-13.draft.jsonl",
    "visual": "document-structure-visual-qa.jsonl",
}


def _normalize_filename(s: str) -> str:
    """"refined_" 접두어 제거 + 연속 공백을 1칸으로 정규화."""
    s = s.removeprefix("refined_")
    return re.sub(r"\s+", " ", s).strip()


def _load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _map_labels(labels: list[str], corpus_norm: dict[str, str]) -> tuple[list[str], list[str]]:
    """사람이 읽을 수 있는 파일명 리스트를 정규화해서 코퍼스 doc_id로 매핑.
    반환값: (매핑된 코퍼스 doc_id 리스트, 매핑 실패한 원본 라벨 리스트)."""
    matched, unmatched = [], []
    for lb in labels:
        norm = _normalize_filename(lb)
        if norm in corpus_norm:
            matched.append(corpus_norm[norm])
        else:
            unmatched.append(lb)
    return matched, unmatched


def load_golden_set_v3(
    corpus_doc_ids: set[str] | None = None, base_dir: Path = GOLDEN_SET_V3_DIR
) -> pd.DataFrame:
    """rag-56/set-13/visual-10 세 lane을 합쳐서 하나의 DataFrame으로 반환한다.

    반환 컬럼: id, query, lane("answer"|"set"), source_lane(원래 lane 이름:
    "answer"|"set"|"visual"), expected_doc_id(list), n_unmatched_labels,
    required_fact_groups, gold_answer, decision, v3_enabled, v3_review_status.

    lane="answer"는 evaluation.py의 evaluate_retrieval()(recall@k/MRR) +
    required_fact_groups로 채점하고, lane="set"은 evaluate_set_retrieval()
    (Precision/Recall/F1)로 채점한다.
    """
    base_dir = Path(base_dir)
    missing = [name for name in _FILES.values() if not (base_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"{base_dir}에 다음 파일이 없습니다: {missing}. "
            "golden-set-v3-share 패키지에서 rag-56.draft.jsonl / set-13.draft.jsonl / "
            "document-structure-visual-qa.jsonl 3개를 이 폴더에 복사해주세요."
        )

    if corpus_doc_ids is None:
        from ..data_processing.merge_text import load_merged

        df = load_merged()
        if df is None:
            raise RuntimeError("output/merged_docs.pkl 캐시가 없습니다. step3_chunking.py를 먼저 실행하세요.")
        corpus_doc_ids = set(df["doc_id"])
    corpus_norm = {_normalize_filename(d): d for d in corpus_doc_ids}

    rows = []
    total_unmatched = 0

    # --- answer(rag-56) ---
    for r in _load_jsonl(base_dir / _FILES["answer"]):
        matched, unmatched = _map_labels(r.get("source_labels", []), corpus_norm)
        total_unmatched += len(unmatched)
        gold = r.get("gold", {})
        rows.append({
            "id": r["case_id"],
            "query": r["question"],
            "lane": "answer",
            "source_lane": "answer",
            "expected_doc_id": matched,
            "n_unmatched_labels": len(unmatched),
            "required_fact_groups": gold.get("required_fact_groups"),
            "gold_answer": gold.get("reference_answer"),
            "decision": gold.get("decision"),
            "v3_enabled": r.get("enabled"),
            "v3_review_status": r.get("review", {}).get("status"),
        })

    # --- set(set-13) ---
    for r in _load_jsonl(base_dir / _FILES["set"]):
        matched, unmatched = _map_labels(r.get("source_labels", []), corpus_norm)
        total_unmatched += len(unmatched)
        rows.append({
            "id": r["case_id"],
            "query": r["question"],
            "lane": "set",
            "source_lane": "set",
            "expected_doc_id": matched,
            "n_unmatched_labels": len(unmatched),
            "required_fact_groups": r.get("required_fact_groups"),
            "gold_answer": None,
            "decision": None,
            "v3_enabled": r.get("enabled"),
            "v3_review_status": r.get("review", {}).get("status"),
        })

    # --- visual(document-structure-visual-qa) ---
    for r in _load_jsonl(base_dir / _FILES["visual"]):
        filename = r.get("document", {}).get("source_filename", "")
        matched, unmatched = _map_labels([filename] if filename else [], corpus_norm)
        total_unmatched += len(unmatched)
        gold = r.get("gold", {})
        rows.append({
            "id": r["case_id"],
            "query": r["question"],
            "lane": "answer",  # 채점 방식은 답변형과 동일(recall@k/MRR + required_fact_groups)
            "source_lane": "visual",
            "expected_doc_id": matched,
            "n_unmatched_labels": len(unmatched),
            "required_fact_groups": gold.get("required_fact_groups"),
            "gold_answer": gold.get("reference_answer"),
            "decision": gold.get("decision"),
            "v3_enabled": r.get("enabled"),
            "v3_review_status": r.get("review", {}).get("status"),
        })

    result = pd.DataFrame(rows)

    n_not_enabled = int((result["v3_enabled"] == False).sum())  # noqa: E712
    n_draft = int((result["v3_review_status"] == "draft").sum())
    print(
        f"[load_golden_set_v3] {len(result)}건 로드(answer/visual {sum(result['lane']=='answer')}건 + "
        f"set {sum(result['lane']=='set')}건). "
        f"원본 패키지의 core40(40)/corpus_analytics(10) 총 50건은 우리 코퍼스와 매칭할 방법이 없어서 제외."
    )
    if n_not_enabled or n_draft:
        print(
            f"[load_golden_set_v3] 참고: enabled=False {n_not_enabled}건, review.status=draft {n_draft}건 "
            "(패키지 자체가 아직 팀 승인 전이라고 명시한 항목들 - 그래도 그대로 평가에 포함시켰음, "
            "v3_enabled/v3_review_status 컬럼으로 나중에 필터링 가능)"
        )
    if total_unmatched:
        print(f"[load_golden_set_v3] 참고: 파일명이 우리 코퍼스와 매칭 안 된 라벨 {total_unmatched}건(n_unmatched_labels 컬럼 참고)")

    return result
