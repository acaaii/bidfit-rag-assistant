"""27단계(신규): step26 스트림릿 화면의 "🤖 AI 요약" 기능이 쓰는 헬퍼 -
step24 정규식 후보가 찾은 위치를 chunks.pkl의 실제 chunk와 매칭해서(찾으면
parent chunk까지 확장), 그 chunk 텍스트를 근거로 gpt-5-mini에게 질문을
던져 깔끔한 답변을 받는다.

[배경 - 2026-09-09] step26을 한빈한테 보여줬더니 "후보들의 parent chunk까지
가져와서 generation 모델이 저 기본 질문들에 답하게 할 수는 없나?"는 질문이
나왔다. 확인해보니:
  - chunking.py의 parent chunk는 표 있는 문서(doc_type=="table_heavy" 또는
    n_tables>0)에만 만들어진다 - 표 없는 문서는 parent 없이 "recursive"
    chunk 하나뿐이다. 샌드박스 코퍼스(output/chunks.pkl)는 98건 전부
    recursive뿐이라(step24 docstring에 이미 적어둔 "n_tables=0 비대표 표본"
    문제가 여기도 그대로 나타남) 이 기능의 parent-확장 경로 자체는 샌드박스
    에서 완전히 재현 검증할 방법이 없다 - 실제 코퍼스(표 있는 문서 포함)
    에서만 최종 확인 가능. 로직(청크 찾기/컨텍스트 조립/LLM 호출)은
    recursive-only 문서로도 검증했다.
  - Chunk 객체엔 원문 문자 위치(offset)가 없어서, 후보 텍스트의 일부(anchor)를
    그 문서의 chunk들 안에서 부분 문자열로 찾아 위치를 특정하는 방식을 썼다.
    RecursiveCharacterTextSplitter가 만든 chunk 경계 때문에 못 찾는 경우도
    있을 수 있어서(청킹 전 clean_text_for_chunking()이 목차/페이지번호 줄을
    지우는 것도 원인이 될 수 있음), 못 찾으면 정규식이 이미 뽑아둔 원문
    후보 텍스트 자체를 컨텍스트로 그대로 쓰는 폴백을 뒀다 - 이러면 항상
    답변 시도는 되고, chunk 매칭에 성공한 경우에만 "더 넓은 근거"(특히 parent
    chunk 확장)의 이득을 본다.
  - 한빈이 "원문 후보는 그대로 두고 AI 요약을 추가로 보여주자"(대체 아님),
    "9개 항목(신청서식 2종 + 새로 추가한 7개) 전부 적용"을 선택해서 그렇게
    구현했다 - 지금까지 지켜온 "후보만 보여주고 사람이 확인" 철학은 유지하고,
    그 위에 참고용으로 LLM 요약을 얹는 것.

API 호출 있음(gpt-5-mini, OpenAI Responses API) - 버튼을 눌러야만 호출되고
(자동 호출 아님), 컨텍스트가 chunk 1~3개(최대 800~2400자 x 3)로 이미 좁혀져
있어서 매 호출 비용/시간이 작다.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_processing.chunking import Chunk, load_chunks  # noqa: E402
from src.generation.generation import build_context, generate_answer  # noqa: E402

_MAX_CONTEXT_CHUNKS = 3  # 후보가 여러 개 매칭돼도 컨텍스트에는 최대 이만큼만(비용/시간 억제)


@lru_cache(maxsize=1)
def _all_chunks() -> list[Chunk]:
    chunks = load_chunks()
    return chunks or []


def get_doc_chunks(doc_id: str) -> list[Chunk]:
    """이 문서의 chunk만(strategy 무관, parent 조회용으로 parent도 포함) 골라서 반환."""
    return [c for c in _all_chunks() if c.doc_id == doc_id]


def _anchor(candidate_text: str, length: int = 50) -> str:
    """후보 텍스트 중간 부분을 짧게 잘라 anchor로 쓴다 - window 전체(150~400자)를
    그대로 찾으면 chunk 경계에 걸려 못 찾을 위험이 크지만, 중간의 짧은 조각은
    하나의 chunk(최소 800자) 안에 온전히 들어있을 가능성이 훨씬 높다."""
    t = candidate_text.strip()
    if len(t) <= length:
        return t
    start = max(0, (len(t) - length) // 3)  # 살짝 앞쪽에 가중 - 헤딩류는 매치가 window 시작 부분에 있음
    return t[start:start + length]


def _find_chunk_context(doc_chunks: list[Chunk], candidate_text: str) -> str | None:
    """candidate_text의 anchor가 들어있는 chunk를 찾는다. child면 parent chunk로
    확장(한빈이 요청한 "parent chunk까지 가져와서"를 구현하는 부분). 못 찾으면 None."""
    anchor = _anchor(candidate_text)
    if len(anchor) < 8:  # 너무 짧은 anchor는 오탐 위험이 커서 시도 안 함
        return None
    by_id = {c.chunk_id: c for c in doc_chunks}
    for c in doc_chunks:
        if c.strategy == "parent":
            continue  # parent는 child 매칭 결과로만 간접적으로 씀(직접 anchor 검색 대상 아님 - child가 더 정밀)
        if anchor in c.text:
            if c.strategy == "child" and c.parent_chunk_id and c.parent_chunk_id in by_id:
                return by_id[c.parent_chunk_id].text  # "검색은 작게, context는 크게" - parent 전체로 확장
            return c.text  # recursive(parent 없는 문서) 또는 예외적으로 strategy 미상
    return None


def build_llm_context(doc_id: str, doc_chunks: list[Chunk], candidate_texts: list[str]) -> tuple[str, bool]:
    """여러 후보 텍스트를 받아 각각 chunk 매칭을 시도하고, 매칭된 chunk 텍스트를
    중복 없이 모아 컨텍스트 문자열로 조립한다. 반환값: (컨텍스트, chunk 매칭에
    하나라도 성공했는지). 하나도 못 찾으면 후보 원문 그대로를 컨텍스트로 써서
    항상 답변 시도가 가능하게 한다(폴백)."""
    matched_texts: list[str] = []
    seen: set[str] = set()
    for cand in candidate_texts:
        ctx = _find_chunk_context(doc_chunks, cand)
        if ctx and ctx not in seen:
            seen.add(ctx)
            matched_texts.append(ctx)
        if len(matched_texts) >= _MAX_CONTEXT_CHUNKS:
            break

    if matched_texts:
        hits = [SimpleNamespace(doc_id=doc_id, text=t) for t in matched_texts]
        return build_context(hits), True

    # 폴백: chunk 매칭 전부 실패 - 정규식이 뽑아둔 원문 후보 그대로를 근거로 사용
    hits = [SimpleNamespace(doc_id=doc_id, text=t) for t in candidate_texts[:_MAX_CONTEXT_CHUNKS]]
    return build_context(hits), False


def generate_quick_answer(
    client, doc_id: str, question: str, candidate_texts: list[str], model: str | None = None
) -> tuple[str | None, bool]:
    """quick-reply 질문 하나에 대해 LLM 요약 답변을 생성한다.
    반환값: (답변 또는 None(생성 실패), chunk 매칭 성공 여부 - UI에 "더 넓은
    근거로 답함" 여부를 표시하는 데 씀).

    [2026-09-10] model 인자 추가 - 서빙 화면에서 gpt-5-nano/mini를 바꿔가며
    비교할 수 있게 하기 위함. 안 넘기면 예전처럼 generation.py의 기본 모델을
    그대로 쓴다(기존 호출부는 수정 불필요)."""
    if not candidate_texts:
        return None, False
    doc_chunks = get_doc_chunks(doc_id)
    context, matched = build_llm_context(doc_id, doc_chunks, candidate_texts)
    answer = generate_answer(client, question, context, **({"model": model} if model else {}))
    return answer, matched
