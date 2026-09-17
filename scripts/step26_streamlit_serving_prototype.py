"""26단계(신규): "서빙 화면" 스트림릿 프로토타입 - 채팅창 느낌으로 문서를
고르고, 자주 물어보는 질문을 버튼으로 눌러서 step24가 만든 후보 추출기
결과를 바로 확인한다.

[배경 - 2026-09-09] step24~25b에서 예산/일정/신청서식 + 새로 추가한 8개
항목(하도급공동수급/사업목적요약/평가배점/계약방식/참가자격/계약보증금/
발주기관/문의처) 추출기를 다 만들고 전체 코퍼스로 히트율까지 확인했다.
이제 이걸 실제로 팀/사용자가 눌러볼 수 있는 화면으로 옮기는 단계 - 한빈이
"서빙 화면 = 스트림릿"이라고 확정했고, 첫 프로토타입 범위는 (1) 버튼
패널만(자유 질문 RAG 채팅은 다음 단계로 미룸 - API 비용/인덱싱 시간 없이
바로 돌려볼 수 있게), (2) 문서는 기존 코퍼스 98건 중에서 고르는 것으로
정했다.

이 화면이 하는 일:
  - 왼쪽 사이드바에서 검색어로 문서를 필터링해서 하나를 고른다.
  - 채팅 형식으로 "이 문서에 대해 자주 물어보는 질문"을 버튼으로 보여준다
    (예산/일정, 발주기관, 신청 서식 2종, 하도급/공동수급, 사업목적 요약,
    계약방식, 참가자격, 계약보증금, 문의처, 평가배점 - 총 11개).
  - 버튼을 누르면 "사용자가 그 질문을 한 것"처럼 채팅 말풍선이 생기고,
    바로 아래에 step24 추출기가 찾은 후보(들)를 어시스턴트 말풍선으로
    보여준다. 여러 버튼을 눌러보면 대화하듯 위로 쌓인다.
  - 자동으로 답 하나를 확정하지 않고 "후보"를 그대로 보여주는 지금까지의
    철학을 화면에도 그대로 반영했다 - 후보가 여러 개면 다 보여주고, 후보가
    아예 없으면 "이 문서엔 없거나 표현이 달라서 못 찾았을 수 있다"고
    명시한다.
  - 자유 질문 입력창은 비활성 상태로 두고 "다음 단계에서 추가 예정"이라고
    안내만 한다(이번 프로토타입 범위 밖 - RAG 검색/생성 연결은 다음 단계).

[2026-09-09 추가 - 🤖 AI 요약] 한빈이 "후보들의 parent chunk까지 가져와서
generation 모델이 저 기본 질문들에 답하게 할 수는 없나?"라고 물어봐서, 신청
서식 2종 + 새로 추가한 7개 항목(총 9개 - 예산/일정/발주기관은 이미 정제된
값이라 제외) 밑에 "🤖 AI 요약 보기" 버튼을 추가했다. 정규식 후보는 그대로
남겨두고(지금까지의 "후보만 보여주고 사람이 확인" 철학 유지 - 한빈이 대체가
아니라 추가를 선택함), 그 버튼을 누르면 step27_quick_answer_llm_polish가
후보 텍스트를 chunks.pkl의 실제 chunk와 매칭해서(찾으면 parent chunk까지
확장 - "검색은 작게, context는 크게") gpt-5-mini에게 넘겨 정리된 답변을
받아온다. 자동 호출이 아니라 버튼을 눌러야만 API가 호출되니 비용은 원하는
만큼만 쓴다. OPENAI_API_KEY가 없으면 버튼이 비활성화되고 이유를 안내한다.

[2026-09-09 추가 - .env 지원] 터미널마다 환경변수를 다시 설정해야 하는
번거로움(+ PyCharm Run Configuration에만 넣어둔 키는 터미널 `streamlit run`
에 안 물려받는 문제) 때문에, 프로젝트 루트에 .env 파일(OPENAI_API_KEY=sk-...
한 줄)을 두면 자동으로 읽어오게 했다. `pip install python-dotenv` 한 번만
설치하면 된다 - 없어도 앱은 죽지 않고 기존처럼 OS 환경변수 방식이 폴백으로
동작한다. .env는 API 키가 든 파일이니 .gitignore에 반드시 추가할 것.

실행 방법(터미널에서):
    streamlit run scripts/step26_streamlit_serving_prototype.py

output/merged_docs.pkl, output/chunks.pkl 캐시가 있어야 빠르게 뜬다(없으면
최초 1회 재계산). 버튼 패널 자체는 API 호출 없음(0원) - "🤖 AI 요약 보기"를
누른 경우에만 gpt-5-mini 호출 1건(짧은 컨텍스트라 비용/시간 작음).

[2026-09-09 추가 - 자유 질문] "이제 자유 질문까지 해 보자"는 요청으로 하단의
비활성 입력창을 실제 채팅창으로 바꿨다. 지금 선택된 문서 하나로 범위를
좁혀서(전체 코퍼스 검색은 더 큰 작업이라 다음 단계로 미룸 - 지금 화면
자체가 "문서 하나 골라서 그 문서에 대해 물어본다"는 구조라 자연스럽게
맞음), 그 문서의 chunk만으로 임시(비영구) HybridIndex를 만들어 실제
벡터+BM25 하이브리드 검색(src/retrieval/indexing.py - 이 프로젝트가 원래
평가·튜닝해온 방식 그대로, step24처럼 정규식으로 새로 만든 게 아니라
기존 검증된 컴포넌트를 그대로 재사용)을 하고, top-k를 parent chunk까지
확장해서 gpt-5-mini에게 넘겨 답을 받는다. 임베딩 백엔드(기본 KURE-v1)는
세션당 한 번만 로드해서 캐싱하고(문서를 바꿔도 모델 재로딩은 없음, 그
문서의 chunk 재인코딩만 매번 일어남 - 몇 개 안 되니 빠름), 인덱스 자체는
문서별로 캐싱한다. 답변 밑에 "근거 보기"를 접어서 실제 검색된 chunk를
보여줘 - 버튼 후보와 마찬가지로 "그냥 믿지 말고 근거를 확인"하는 철학
유지. 질문마다 임베딩(로컬, 무료) + gpt-5-mini 호출 1건(유료, AI 요약
버튼과 같은 비용 구조)이 든다. 네트워크가 막힌 이 샌드박스에서는 KURE-v1
다운로드가 실패해 embeddings.py의 TfidfHashEmbedding 폴백(어휘 기반
근사치)으로 동작을 검증했다 - 실제 KURE-v1 품질 검증은 이미 모델이
받아져 있는 한빈 실제 환경에서 확인해야 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# [2026-09-09 추가] 터미널마다 OPENAI_API_KEY를 다시 export/set 해줘야 하는
# 번거로움 때문에(PyCharm Run Configuration에만 넣어둔 키는 터미널의
# `streamlit run`에는 안 물려받아 자주 놓친다) 프로젝트 루트의 .env 파일을
# 자동으로 읽어오게 했다 - 있으면 읽고, 없으면 조용히 넘어가서 기존처럼
# OS 환경변수 방식도 그대로 동작한다. python-dotenv가 아직 설치 안 됐어도
# (pip install python-dotenv) 앱 자체는 죽지 않고 그냥 .env 지원만 빠진다.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import streamlit as st  # noqa: E402

from src.data_processing.load_metadata import load_clean_metadata  # noqa: E402
from src.data_processing.merge_text import load_merged, merge_all, save_merged  # noqa: E402

from src.generation.generation import build_context, generate_answer  # noqa: E402

from step27_quick_answer_llm_polish import generate_quick_answer, get_doc_chunks  # noqa: E402

from step24_prefilled_qa_prototype import (  # noqa: E402
    extract_bond_candidates,
    extract_contact_candidates,
    extract_contract_method_candidates,
    extract_eligibility_candidates,
    extract_eval_score_candidates,
    extract_form_list,
    extract_purpose_candidates,
    extract_spec_candidates,
    extract_subcontract_candidates,
    format_budget_schedule,
    format_issuer,
)

st.set_page_config(page_title="입찰메이트 - 서빙 화면 프로토타입", layout="wide")


# --- 데이터 로드 (캐시) ---
@st.cache_data(show_spinner="문서 코퍼스를 불러오는 중...")
def _load_corpus():
    merged = load_merged()
    if merged is None:
        merged = merge_all(load_clean_metadata())
        save_merged(merged)
        merged = load_merged()
    return merged


# --- OpenAI 클라이언트 (캐시 - 한 번만 생성 시도) ---
@st.cache_resource
def _get_openai_client():
    """(client, error_message) 튜플을 반환한다. OPENAI_API_KEY가 없거나
    openai 패키지가 없으면 client는 None이고 error_message에 이유가 담긴다 -
    "🤖 AI 요약 보기" 버튼을 누른 사람에게 그 이유를 그대로 보여주기 위함
    (버튼 자체가 없어져서 왜 없는지 모르게 하는 것보다, 눌러보면 이유를
    바로 알 수 있는 쪽이 낫다고 판단)."""
    try:
        from openai import OpenAI
        return OpenAI(), None
    except Exception as e:  # noqa: BLE001
        return None, (
            "OpenAI 클라이언트를 만들지 못했어요 - OPENAI_API_KEY가 안 잡혀요. "
            "프로젝트 루트에 .env 파일을 만들고 그 안에 OPENAI_API_KEY=sk-...를 "
            "한 줄 적어주세요(python-dotenv 설치 필요: pip install python-dotenv). "
            f"(원본 에러: {e})"
        )


# --- 임베딩 백엔드 (캐시 - 세션당 한 번만 모델 로드) ---
@st.cache_resource(show_spinner="검색용 임베딩 모델을 불러오는 중... (처음 한 번만, 몇 분 걸릴 수 있어요)")
def _get_embedding_backend():
    from src.retrieval.embeddings import get_default_embedding_backend
    return get_default_embedding_backend()


# --- 문서별 검색 인덱스 (캐시 - 문서당 한 번만 빌드, 비영구/임시) ---
@st.cache_resource(show_spinner="이 문서를 검색 인덱스에 반영하는 중...")
def _get_doc_index(doc_id: str):
    """지금 선택된 문서의 chunk만으로 임시 HybridIndex를 만든다(persist=False -
    디스크에 안 남기고 메모리에서만, 문서를 바꾸면 이전 인덱스는 그냥 버려짐).
    임베딩 백엔드는 _get_embedding_backend()로 세션 전체가 공유해서 문서를
    바꿔도 모델을 다시 로드하지 않는다 - 그 문서의 chunk 몇 개만 새로
    인코딩하면 되니 빠르다."""
    from src.retrieval.indexing import HybridIndex
    doc_chunks = get_doc_chunks(doc_id)
    backend = _get_embedding_backend()
    return HybridIndex(doc_chunks, persist=False, embedding_backend=backend)


def _render_sources(hits) -> str:
    if not hits:
        return "_(검색된 근거가 없어요)_"
    parts = []
    for i, h in enumerate(hits, 1):
        preview = h.text.replace("\n", " ").strip()
        if len(preview) > 300:
            preview = preview[:300] + "..."
        parts.append(f"**근거 {i}** _({h.matched_by})_\n\n> {preview}")
    return "\n\n".join(parts)


# --- 후보 리스트(list[dict{method,text}]) -> 마크다운 렌더링 ---
def _render_candidates(candidates: list[dict], empty_hint: str) -> str:
    if not candidates:
        return f"이 문서에서는 후보를 찾지 못했어요. ({empty_hint})"
    parts = []
    for i, c in enumerate(candidates, 1):
        preview = c["text"].replace("\n", " ").strip()
        if len(preview) > 400:
            preview = preview[:400] + "..."
        parts.append(f"**후보 {i}** _(방법: {c['method']})_\n\n> {preview}")
    parts.append(
        f"\n\n*후보 {len(candidates)}개 - 자동으로 하나를 확정하지 않으니 "
        "직접 확인해서 맞는 답을 골라주세요.*"
    )
    return "\n\n".join(parts)


def _render_form_list(forms: list[dict]) -> str:
    if not forms:
        return "이 문서에서는 첨부 서식 목록을 찾지 못했어요. (번호 매겨진 서식이 본문에 없거나 다른 표기 방식일 수 있음)"
    parts = []
    for f in forms:
        parts.append(f"- **{f['label']}**  ...{f['before']} **[서식]** {f['after']}...")
    return "\n".join(parts)


def _form_list_texts(forms: list[dict]) -> list[str]:
    """extract_form_list()의 {label,before,after} 형태를 AI 요약용 후보
    텍스트(chunk 매칭에 쓸 짧은 문자열)로 바꾼다."""
    return [f"{f['before']} {f['label']} {f['after']}" for f in forms]


# --- 버튼 정의: key, 버튼에 보일 라벨, 클릭 시 사용자 말풍선에 보일 질문,
# (row, text) -> 마크다운 문자열을 반환하는 렌더 함수, 그리고 (선택) AI 요약용
# 원문 후보 텍스트 리스트를 반환하는 candidates 함수. candidates가 없는
# 항목(예산/일정, 발주기관)은 이미 정제된 값이라 AI 요약 버튼을 안 붙인다. ---
def _build_quick_replies():
    return [
        {
            "key": "budget_schedule",
            "label": "💰 예산/일정",
            "question": "예산이랑 일정 알려줘",
            "render": lambda row, text: f"```\n{format_budget_schedule(row)}\n```",
        },
        {
            "key": "issuer",
            "label": "🏢 발주기관",
            "question": "발주기관이 어디야?",
            "render": lambda row, text: format_issuer(row),
        },
        {
            "key": "form_list",
            "label": "📎 첨부 서식 목록",
            "question": "첨부해야 하는 서식이 뭐가 있어?",
            "render": lambda row, text: _render_form_list(extract_form_list(text)),
            "candidates": lambda row, text: _form_list_texts(extract_form_list(text)),
        },
        {
            "key": "spec",
            "label": "📝 제안서 작성 규정",
            "question": "제안서 작성 규정(분량/양식) 알려줘",
            "render": lambda row, text: _render_candidates(
                extract_spec_candidates(text), "목차와 본문 구분이 어려운 문서일 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_spec_candidates(text)],
        },
        {
            "key": "subcontract",
            "label": "🤝 하도급/공동수급",
            "question": "하도급이나 공동수급 가능해?",
            "render": lambda row, text: _render_candidates(
                extract_subcontract_candidates(text), "이 문서엔 관련 조항이 없을 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_subcontract_candidates(text)],
        },
        {
            "key": "purpose",
            "label": "🎯 사업목적 요약",
            "question": "이 사업 목적이 뭐야?",
            "render": lambda row, text: _render_candidates(
                extract_purpose_candidates(row, text), "메타데이터 요약도 본문 후보도 없는 문서일 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_purpose_candidates(row, text)],
        },
        {
            "key": "contract_method",
            "label": "📑 계약방식",
            "question": "계약방식/낙찰방식이 뭐야?",
            "render": lambda row, text: _render_candidates(
                extract_contract_method_candidates(text), "이 문서엔 관련 조항이 없을 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_contract_method_candidates(text)],
        },
        {
            "key": "eligibility",
            "label": "✅ 참가자격",
            "question": "참가자격 제한이 있어?",
            "render": lambda row, text: _render_candidates(
                extract_eligibility_candidates(text), "이 문서엔 관련 조항이 없을 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_eligibility_candidates(text)],
        },
        {
            "key": "bond",
            "label": "🏦 계약보증금",
            "question": "계약보증금 비율이 얼마야?",
            "render": lambda row, text: _render_candidates(
                extract_bond_candidates(text),
                "실제로 이 문서 본문에 비율 숫자가 없는 경우가 많음(법령 인용/면제 문구만 있는 경우 등)",
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_bond_candidates(text)],
        },
        {
            "key": "contact",
            "label": "📞 문의처",
            "question": "문의처 연락처 알려줘",
            "render": lambda row, text: _render_candidates(
                extract_contact_candidates(text), "본문에 연락처 정보가 아예 없는 문서일 수 있음"
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_contact_candidates(text)],
        },
        {
            "key": "eval_score",
            "label": "📊 평가배점 [참고용]",
            "question": "평가배점 어떻게 돼?",
            "render": lambda row, text: (
                "*주의: 배점표는 보통 표 형태라 이 위치 힌트만으로는 실제 배점 숫자까지 "
                "정확히 뽑아내기 어려울 수 있어요.*\n\n"
                + _render_candidates(extract_eval_score_candidates(text), "배점표 위치 자체를 못 찾았을 수 있음")
            ),
            "candidates": lambda row, text: [c["text"] for c in extract_eval_score_candidates(text)],
        },
    ]


def main():
    st.title("📋 입찰메이트 - 서빙 화면 프로토타입")
    st.caption(
        "버튼을 누르면 자주 물어보는 질문에 대한 후보 답변을 바로 보여주고, "
        "맨 아래 채팅창에는 이 문서에 대해 자유롭게 질문할 수 있어요."
    )

    merged = _load_corpus()

    with st.sidebar:
        st.header("문서 선택")
        search = st.text_input("문서명 검색", placeholder="예: 한국철도공사")
        doc_ids = merged["doc_id"].tolist()
        if search:
            doc_ids = [d for d in doc_ids if search.strip() in d]
        if not doc_ids:
            st.warning("검색 결과가 없어요.")
            st.stop()
        selected_doc_id = st.selectbox(f"문서 ({len(doc_ids)}건)", doc_ids)
        st.caption(f"전체 코퍼스: {len(merged)}건")

    row = merged[merged["doc_id"] == selected_doc_id].iloc[0]
    text = row.get("text") or ""

    # 문서를 바꾸면 이전 대화 기록은 초기화
    if st.session_state.get("_current_doc_id") != selected_doc_id:
        st.session_state["_current_doc_id"] = selected_doc_id
        st.session_state["_chat_history"] = []

    st.subheader(f"📄 {selected_doc_id}")

    quick_replies = _build_quick_replies()

    with st.chat_message("assistant"):
        st.write("안녕하세요! 이 문서에 대해 자주 물어보는 질문이에요. 궁금한 걸 눌러보세요 👇")

    cols = st.columns(4)
    for i, qr in enumerate(quick_replies):
        if cols[i % 4].button(qr["label"], key=f"btn_{qr['key']}", use_container_width=True):
            answer = qr["render"](row, text)
            candidates_fn = qr.get("candidates")
            candidate_texts = candidates_fn(row, text) if candidates_fn else []
            st.session_state["_chat_history"].append({
                "kind": "button",
                "question": qr["question"],
                "answer": answer,
                "candidate_texts": candidate_texts,  # AI 요약 버튼용 원문(비어있으면 그 버튼 자체를 안 보여줌)
                "llm_answer": None,
                "llm_matched": None,
                "llm_error": None,
            })

    client, client_error = _get_openai_client()

    if st.session_state.get("_chat_history"):
        st.divider()
        for idx, entry in enumerate(st.session_state["_chat_history"]):
            with st.chat_message("user"):
                st.write(entry["question"])
            with st.chat_message("assistant"):
                if entry.get("kind") == "free":
                    if entry.get("error"):
                        st.error(entry["error"])
                    else:
                        st.markdown(entry["answer"])
                        with st.expander(f"🔎 근거 보기 ({len(entry.get('sources') or [])}개)"):
                            st.markdown(_render_sources(entry.get("sources") or []))
                    continue

                st.markdown(entry["answer"])

                if entry["candidate_texts"]:
                    if entry["llm_answer"]:
                        note = (
                            "문단 전체(더 넓은 문맥)를 근거로 답함"
                            if entry["llm_matched"] else "위 원문 후보만 근거로 답함(더 넓은 문맥은 못 찾음)"
                        )
                        st.info(f"🤖 **AI 요약** _({note})_\n\n{entry['llm_answer']}")
                    elif entry["llm_error"]:
                        st.error(f"🤖 AI 요약 실패: {entry['llm_error']}")
                        if st.button("다시 시도", key=f"retry_{idx}"):
                            entry["llm_error"] = None
                            st.rerun()
                    else:
                        if st.button("🤖 AI 요약 보기", key=f"llm_{idx}", disabled=client is None):
                            if client is None:
                                entry["llm_error"] = client_error
                            else:
                                with st.spinner("gpt-5-mini한테 물어보는 중..."):
                                    try:
                                        llm_answer, matched = generate_quick_answer(
                                            client, selected_doc_id, entry["question"], entry["candidate_texts"]
                                        )
                                    except Exception as e:  # noqa: BLE001
                                        llm_answer, matched = None, False
                                        entry["llm_error"] = f"호출 중 오류: {e}"
                                    if llm_answer:
                                        entry["llm_answer"] = llm_answer
                                        entry["llm_matched"] = matched
                                    elif not entry["llm_error"]:
                                        entry["llm_error"] = "모델이 답변을 생성하지 못했어요(네트워크/키 문제일 수 있음)."
                            st.rerun()
                        if client is None:
                            st.caption(f"⚠️ {client_error}")

        if st.button("🗑️ 대화 지우기"):
            st.session_state["_chat_history"] = []
            st.rerun()

    free_question = st.chat_input("이 문서에 대해 자유롭게 질문해보세요 (예: 제출 서류가 뭐가 필요해?)")
    if free_question:
        entry = {"kind": "free", "question": free_question, "answer": None, "sources": [], "error": None}
        if client is None:
            entry["error"] = f"🔎 검색/답변 생성 실패: {client_error}"
        else:
            with st.spinner("문서를 검색하고 답변을 생성하는 중..."):
                try:
                    index = _get_doc_index(selected_doc_id)
                    hits = index.hybrid_search(free_question, k=5, expand_to_parent=True)
                    context = build_context(hits) if hits else "(이 문서에서 관련 내용을 찾지 못함)"
                    answer = generate_answer(client, free_question, context)
                except Exception as e:  # noqa: BLE001
                    entry["error"] = f"🔎 검색/답변 생성 중 오류: {e}"
                else:
                    entry["sources"] = hits
                    if answer:
                        entry["answer"] = answer
                    else:
                        entry["error"] = "🔎 모델이 답변을 생성하지 못했어요(네트워크/키 문제일 수 있음)."
        st.session_state["_chat_history"].append(entry)
        st.rerun()

    with st.expander("ℹ️ 이 프로토타입에 대해"):
        st.markdown(
            "- 위 버튼들은 전부 정규식 기반 후보 추출기(step24)를 그대로 씁니다 - "
            "정답 하나를 자동으로 확정하지 않고, 후보를 보여주면 사람이 직접 확인하는 방식이에요.\n"
            "- 항목별로 전체 코퍼스(98건) 기준 '후보를 하나라도 찾은 문서' 비율은 대략 이래요: "
            "하도급/공동수급·사업목적요약 99~100%, 참가자격 98%, 문의처 90%, 평가배점 89%, "
            "계약방식 88%, 신청서식 85~87%, 계약보증금 44%(계속 개선 중 - 본문에 비율 숫자가 "
            "아예 없는 문서가 많음).\n"
            "- '평가배점'은 배점표가 보통 표 형태라 정규식만으로는 위치 힌트 정도만 가능해요.\n"
            "- 후보가 있는 항목엔 **🤖 AI 요약 보기** 버튼이 같이 떠요 - 후보 텍스트가 속한 chunk를 "
            "찾아서(표 있는 문서는 parent chunk까지 확장) gpt-5-mini에게 넘겨 정리된 답을 받아옵니다. "
            "원문 후보를 대체하는 게 아니라 참고용으로 위에 추가로 보여주는 것 - 버튼을 누를 때만 "
            "API가 호출돼요(자동 호출 아님).\n"
            "- 맨 아래 채팅창은 **이 문서 하나로 범위를 좁힌** 자유 질문이에요(전체 코퍼스 검색은 "
            "아님) - 실제 벡터+BM25 하이브리드 검색(이 프로젝트가 원래 평가·튜닝해온 방식)으로 "
            "관련 부분을 찾아 gpt-5-mini에게 넘겨 답을 만들고, 답변 밑 '근거 보기'에서 실제로 "
            "검색된 부분을 확인할 수 있어요."
        )


if __name__ == "__main__":
    main()
