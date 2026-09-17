"""리랭커(reranking) 추상화 - retrieval 후보 풀을 cross-encoder로 다시 채점해
순위만 재조정하는 2단계 검색.

[배경 - 2026-09-02/03] `HybridIndex.hybrid_search()`(BM25 + 벡터, 둘 다
bi-encoder 계열)는 질의와 문서를 각각 "독립적으로" 인코딩한 뒤 벡터 유사도만
비교한다 - 빠르지만 질의-문서 쌍을 "동시에" 보고 관련도를 판단하지는 못한다.
Cross-encoder 리랭커는 (질의, 문서) 쌍을 통째로 모델에 넣어 관련도 점수를
직접 매기므로 정확도는 더 높지만, 후보 하나하나를 다 forward pass 해야 해서
느리다 - 그래서 "코퍼스 전체"가 아니라 1차 검색(hybrid_search)이 이미 추려낸
후보 풀(top-N, 예: 20개)에만 적용하는 2단계 구조로 쓴다.

중요: 리랭커는 "순위 재조정"만 한다 - 후보 풀 자체를 넓히지 않으므로,
1차 검색이 애초에 후보 풀 안에 정답을 못 넣었으면(예: recall@20 자체가
낮음) 리랭커로도 못 살린다. 그래서 리랭킹의 효과는 "후보 풀 크기(N) 기준
recall"이 아니라 "그 안에서 top-1/top-3처럼 상위에 얼마나 정확히
배치하는지"(정밀도)로 나타난다 - scripts/step15_rerank_compare.py가 같은
후보 풀(hybrid_search 결과)에 대해 리랭킹 전/후를 나란히 비교하는 이유.

기본 모델은 BAAI/bge-reranker-v2-m3(다국어 cross-encoder, sentence-transformers
CrossEncoder로 바로 로드됨, 로컬 실행·무료). KURE-v1(BGE 계열 파인튜닝)과
같은 BGE 계열이라 궁합이 좋고, 임베딩 A/B 때(step11) 확인한 대로 이
코퍼스에서는 한국어 특화/다국어 로컬 모델이 유료 API보다 나았던 경험과도
방향이 맞아서 1차 후보로 골랐다(2026-09-03, 한빈 선택).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .indexing import SearchHit

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class Reranker(ABC):
    name: str = "base"

    @abstractmethod
    def score(self, query: str, texts: list[str]) -> list[float]:
        """query와 texts의 각 원소를 (query, text) 쌍으로 채점한다.
        점수가 높을수록 관련도가 높다는 뜻이어야 한다(모델마다 스케일이
        달라도 상관없음 - 이 안에서는 상대적 순위만 쓴다)."""
        ...

    def rerank(self, query: str, hits: list[SearchHit], top_k: int | None = None) -> list[SearchHit]:
        """hits를 score() 기준으로 다시 정렬한 새 리스트를 반환한다(원본은
        건드리지 않음). score는 리랭커 점수로 덮어쓰고, matched_by 끝에
        "+rerank"를 붙여 재정렬이 적용됐다는 걸 알 수 있게 한다.
        top_k를 주면 상위 top_k개만 자르고, None이면 전체를 재정렬만 해서
        그대로 반환한다(평가 코드에서 이후 단계가 자체적으로 자르는 경우 대비)."""
        if not hits:
            return hits
        scores = self.score(query, [h.text for h in hits])
        order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
        reranked = [
            SearchHit(
                chunk_id=hits[i].chunk_id,
                doc_id=hits[i].doc_id,
                text=hits[i].text,
                metadata=hits[i].metadata,
                score=float(scores[i]),
                matched_by=f"{hits[i].matched_by}+rerank",
            )
            for i in order
        ]
        return reranked[:top_k] if top_k is not None else reranked


class CrossEncoderReranker(Reranker):
    """sentence-transformers의 CrossEncoder 래퍼. HuggingFace Hub에서
    모델을 내려받는다(BAAI/bge-reranker-v2-m3는 약 2.2GB, 최초 실행 시
    회선에 따라 수 분 걸릴 수 있음 - KURE-v1 다운로드와 비슷한 규모)."""

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)
        self.name = model_name

    def score(self, query: str, texts: list[str]) -> list[float]:
        pairs = [[query, t] for t in texts]
        return [float(s) for s in self._model.predict(pairs, show_progress_bar=False)]
