"""입찰메이트 서빙 앱 - FastAPI 백엔드.

[배경 - 2026-09-09] `scripts/step26_streamlit_serving_prototype.py`(스트림릿
프로토타입)로 화면 흐름을 먼저 검증했고, 이제 "앱처럼" 상시 서빙하기 위해
FastAPI + 순수 HTML/JS 조합으로 옮겼다. 기능은 스트림릿 프로토타입과 동일하다:

  1. 문서를 하나 고르고,
  2. 자주 묻는 질문 11종을 버튼으로 눌러 step24 정규식 후보를 확인하고,
  3. (선택) "AI 요약" 버튼으로 그 후보가 속한 chunk(표 있는 문서는 parent
     chunk까지 확장)를 근거로 gpt-5-mini 요약을 받고,
  4. 맨 아래 채팅창에서 그 문서 범위로 좁힌 자유 질문을 한다
     (기존 HybridIndex 벡터+BM25 검색 그대로 재사용).

[2026-09-10 추가] 화면 상단에서 생성 모델(gpt-5-nano/mini 등)을 바꿔가며 쓸 수 있고,
답변마다 어떤 모델이 몇 초 걸렸는지 같이 표시한다 - 모델 선택을 측정으로 하는
이 프로젝트 방식(README 6장)을 화면에서도 그대로 할 수 있게 하려는 것.
그리고 후보 밑의 "원문 더 보기"는 그 후보 주변 원문을 더 넓게 다시 잘라 보여준다
(원문이 서버에 이미 있으므로 API 호출도 추가 비용도 없다).

스트림릿과 다른 점은 "상태를 서버가 아니라 브라우저가 들고 있다"는 것뿐이다 -
대화 기록은 프론트(app/static/index.html)의 자바스크립트 배열에 쌓이고,
서버는 요청 하나하나에 답만 하는 stateless API다. 덕분에 여러 명이 동시에
붙어도 서로의 화면이 섞이지 않는다(스트림릿 session_state는 세션 단위라
같은 방식으로 만들려면 별도 처리가 필요했다).

무거운 자산(코퍼스 pkl, KURE-v1 임베딩 모델, 문서별 인덱스)은 전부 **지연
로드 + 프로세스 내 캐시**다:
  - 코퍼스(merged_docs.pkl)만 첫 요청 때 한 번 읽는다(수 초).
  - 임베딩 모델은 "자유 질문"을 실제로 처음 던졌을 때만 로드한다 - 버튼 패널만
    쓸 거면 2GB 모델을 아예 안 올린다. 컨테이너 기동도 그만큼 빨라서
    헬스체크가 바로 통과한다.
  - 문서별 임시 인덱스(persist=False)는 최대 _MAX_CACHED_INDEXES개까지만 들고
    있다가 오래된 것부터 버린다(문서 100건을 다 돌면 메모리가 계속 늘어나는 걸
    막기 위함).
동시 요청이 같은 자원을 두 번 로드하지 않도록 락으로 감쌌다.

실행:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
    (도커로 띄우는 게 기본 - 루트의 Dockerfile / docker-compose.yml 참고)

필요한 것:
  - output/merged_docs.pkl, output/chunks.pkl (전처리 캐시)
  - .env 의 OPENAI_API_KEY (AI 요약/자유 질문에만 필요 - 버튼 패널은 없어도 동작)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
# step24(정규식 후보 추출기)/step27(AI 요약 헬퍼)을 그대로 재사용한다 - 서빙용으로
# 복사해서 두 벌이 되면 한쪽만 고쳐지는 사고가 나므로, scripts/를 import 경로에
# 넣어 원본 하나만 쓴다.
sys.path.insert(0, str(BASE_DIR / "scripts"))

# .env는 "이 파일 기준 레포 루트"에서 찾는다 - 실행 위치(cwd)와 무관하다.
# 어느 경로를 봤는지/찾았는지를 기동 로그와 /api/status에 그대로 남긴다.
# (다른 폴더에 .env를 두고 "키를 넣었는데 왜 안 되지"로 헤매기 딱 좋은 부분이라,
#  조용히 실패하지 않게 만들어둔 것)
DOTENV_PATH = BASE_DIR / ".env"
DOTENV_FOUND = DOTENV_PATH.exists()
try:
    from dotenv import load_dotenv

    PYTHON_DOTENV_INSTALLED = True
    load_dotenv(DOTENV_PATH)
except ImportError:  # python-dotenv 미설치 - OS 환경변수 방식으로 계속 동작
    PYTHON_DOTENV_INSTALLED = False

print(
    f"[app] .env 경로={DOTENV_PATH} 존재={DOTENV_FOUND} "
    f"python-dotenv={PYTHON_DOTENV_INSTALLED} "
    f"OPENAI_API_KEY={'감지됨' if os.environ.get('OPENAI_API_KEY') else '없음'}",
    flush=True,
)

import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.config import MERGED_DOCS_PATH  # noqa: E402

# --- 레거시 pickle 호환 shim -------------------------------------------------
# output/chunks.pkl 안의 Chunk 객체는 "그 pkl을 만들 때의 모듈 경로"를 그대로
# 기억한다. src/가 평면 구조(src/chunking.py)이던 시절에 만든 캐시는
# "src.chunking"을 찾는데, 지금 구조에는 src/data_processing/chunking.py만
# 있어서 그대로 열면 ModuleNotFoundError가 난다. 캐시를 다시 만들면(수 시간)
# 해결되지만, 이미 만들어둔 pkl을 그대로 쓸 수 있게 옛 이름을 새 모듈로
# 연결해둔다. 새로 만든 pkl에는 아무 영향이 없다.
import src.data_processing.chunking as _chunking_module  # noqa: E402

sys.modules.setdefault("src.chunking", _chunking_module)

from src.generation.generation import (  # noqa: E402
    DEFAULT_GENERATION_MODEL,
    build_context,
    generate_answer,
)
from step24_prefilled_qa_prototype import (  # noqa: E402
    _snap_bounds,
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
from step27_quick_answer_llm_polish import generate_quick_answer  # noqa: E402

_MAX_CACHED_INDEXES = 16  # 문서별 임시 인덱스를 이 개수까지만 들고 있는다

# --- 생성 모델 선택 ---------------------------------------------------------
# [2026-09-10 추가] 화면에서 모델을 바꿔가며 비교할 수 있게 했다. 이 프로젝트는
# "선호가 아니라 측정으로 고른다"를 계속 지켜왔으니(README 6장), 화면에서도
# 모델 이름과 응답 시간을 답변마다 같이 보여줘서 nano/mini 차이를 눈으로 바로
# 비교할 수 있게 한다.
#
# 기본값은 지금까지 쓰던 gpt-5-mini 그대로다(모델을 안 고르면 동작이 예전과 동일).
# 목록에 mini/nano만 둔 이유는 지금 팀 API 키로 부를 수 있는 게 이 둘뿐이기
# 때문이다(2026-09-10 한빈 확인). 나중에 쓸 수 있는 모델이 늘면 BIDFIT_MODELS
# 환경변수로 목록을 바꾸면 된다 - 예: BIDFIT_MODELS=gpt-5-nano,gpt-5-mini
# (docker-compose.yml에서 그대로 넘겨받는다). 임의 문자열을 그대로 API에 던지지
# 않고 이 목록 안에서만 허용해서, 오타 때문에 호출이 실패하는 걸 미리 막는다.
_DEFAULT_MODEL_CHOICES = ["gpt-5-mini", "gpt-5-nano"]


def _model_choices() -> list[str]:
    raw = os.environ.get("BIDFIT_MODELS", "")
    items = [m.strip() for m in raw.split(",") if m.strip()] or list(_DEFAULT_MODEL_CHOICES)
    if DEFAULT_GENERATION_MODEL not in items:
        items.insert(0, DEFAULT_GENERATION_MODEL)
    return items


MODEL_CHOICES = _model_choices()


def _resolve_model(model: str | None) -> str:
    if not model:
        return DEFAULT_GENERATION_MODEL
    if model not in MODEL_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 모델입니다: {model} (선택 가능: {', '.join(MODEL_CHOICES)})",
        )
    return model

app = FastAPI(title="입찰메이트 서빙 API", version="0.1.0")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# =============================================================================
# 지연 로드되는 무거운 자원들 (전부 프로세스 내 1회 로드 + 락으로 중복 로드 방지)
# =============================================================================
_corpus_lock = threading.Lock()
_backend_lock = threading.Lock()
_index_lock = threading.Lock()
_client_lock = threading.Lock()

_corpus: pd.DataFrame | None = None
_corpus_mode: str | None = None  # "full"(재평가 적용) | "raw"(pkl 그대로)
_corpus_error: str | None = None

_backend = None
_backend_error: str | None = None

_indexes: "OrderedDict[str, Any]" = OrderedDict()

_client = None
_client_error: str | None = None
_client_tried = False


def get_corpus() -> pd.DataFrame:
    """전처리 캐시(output/merged_docs.pkl)를 읽어 DataFrame으로 돌려준다.

    가능하면 `load_merged()`를 쓴다 - 단순히 pkl을 읽는 게 아니라 예산/마감일
    결측 재평가와 사람이 확정한 override(data/budget_overrides.csv 등)까지
    다시 반영해주기 때문이다. 그런데 이 함수를 import하려면 hwp/pdf 파서
    의존성(pyhwp, PyMuPDF, pdfplumber...)이 전부 설치돼 있어야 한다 -
    서빙 이미지를 가볍게 만들려고 그걸 뺀 환경에서도 앱이 죽지 않도록,
    실패하면 pkl을 그대로 읽는 방식으로 폴백하고 그 사실을 /api/status에
    그대로 노출한다(조용히 다르게 동작하는 게 제일 위험하므로).
    """
    global _corpus, _corpus_mode, _corpus_error
    if _corpus is not None:
        return _corpus
    with _corpus_lock:
        if _corpus is not None:
            return _corpus
        if not MERGED_DOCS_PATH.exists():
            _corpus_error = (
                f"전처리 캐시가 없습니다: {MERGED_DOCS_PATH}. "
                "먼저 python scripts/step2_merge_text.py 로 만들어서 "
                "output/ 볼륨에 넣어주세요(서버가 원본 hwp/pdf를 직접 "
                "재파싱하지는 않습니다 - 수 시간짜리 배치 작업이라 서빙 중에 "
                "할 일이 아닙니다)."
            )
            raise HTTPException(status_code=503, detail=_corpus_error)
        try:
            from src.data_processing.merge_text import load_merged

            df = load_merged()
            mode = "full"
        except Exception as e:  # noqa: BLE001 - 파서 의존성 없음 등
            df = pd.read_pickle(MERGED_DOCS_PATH)
            mode = "raw"
            _corpus_error = (
                f"load_merged()를 쓰지 못해 pkl을 그대로 읽었습니다({e}). "
                "예산/마감일 override 재적용이 빠진 상태입니다."
            )
        _corpus, _corpus_mode = df, mode
        return _corpus


def get_embedding_backend():
    """KURE-v1(약 2GB) 임베딩 백엔드. 자유 질문을 처음 던질 때만 로드된다."""
    global _backend, _backend_error
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        from src.retrieval.embeddings import get_default_embedding_backend

        try:
            _backend = get_default_embedding_backend()
        except Exception as e:  # noqa: BLE001
            _backend_error = str(e)
            raise
        return _backend


def get_doc_index(doc_id: str):
    """이 문서의 chunk만으로 만든 임시 HybridIndex(persist=False).

    임베딩 백엔드는 프로세스 전체가 공유하므로 문서를 바꿔도 모델을 다시
    로드하지 않는다 - 그 문서 chunk 몇 개만 새로 인코딩하면 된다.
    """
    with _index_lock:
        if doc_id in _indexes:
            _indexes.move_to_end(doc_id)
            return _indexes[doc_id]

    from step27_quick_answer_llm_polish import get_doc_chunks

    from src.retrieval.indexing import HybridIndex

    doc_chunks = get_doc_chunks(doc_id)
    if not doc_chunks:
        raise HTTPException(
            status_code=503,
            detail=(
                "이 문서의 chunk를 찾지 못했습니다 - output/chunks.pkl이 없거나 "
                "이 문서가 빠져 있을 수 있어요(버튼 패널은 chunk 없이도 동작합니다)."
            ),
        )
    backend = get_embedding_backend()
    index = HybridIndex(doc_chunks, persist=False, embedding_backend=backend)

    with _index_lock:
        _indexes[doc_id] = index
        _indexes.move_to_end(doc_id)
        while len(_indexes) > _MAX_CACHED_INDEXES:
            _indexes.popitem(last=False)
    return index


def get_openai_client():
    """(client, error) - 키가 없으면 client=None이고 error에 이유가 담긴다.

    버튼 패널은 키 없이도 전부 동작하므로 여기서 앱을 죽이지 않고, AI 요약/
    자유 질문을 실제로 요청했을 때만 이 이유를 그대로 사용자에게 보여준다.
    """
    global _client, _client_error, _client_tried
    if _client_tried:
        return _client, _client_error
    with _client_lock:
        if _client_tried:
            return _client, _client_error
        try:
            from openai import OpenAI

            _client = OpenAI()
        except Exception as e:  # noqa: BLE001
            _client = None
            if not DOTENV_FOUND and not os.environ.get("OPENAI_API_KEY"):
                why = f"{DOTENV_PATH} 파일이 없습니다(이 경로에 있어야 합니다 - 다른 폴더의 .env는 읽지 않습니다)."
            elif DOTENV_FOUND and not PYTHON_DOTENV_INSTALLED:
                why = ".env는 있지만 python-dotenv가 설치돼 있지 않아 읽지 못했습니다(pip install python-dotenv)."
            elif DOTENV_FOUND:
                why = f"{DOTENV_PATH}는 있는데 OPENAI_API_KEY 값을 읽지 못했습니다(줄 형식이 OPENAI_API_KEY=sk-... 인지, 파일 인코딩이 UTF-8인지 확인해주세요)."
            else:
                why = "OPENAI_API_KEY 환경변수가 없습니다."
            _client_error = f"{why} 값을 넣은 뒤에는 서버를 다시 시작해야 반영됩니다. (원본 에러: {e})"
        _client_tried = True
        return _client, _client_error


# =============================================================================
# 자주 묻는 질문 11종 - step26의 버튼 정의를 그대로 옮긴 것.
# 각 항목은 (blocks, candidate_texts)를 돌려준다. blocks는 프론트가 그대로
# 그릴 수 있는 구조(JSON)이고, candidate_texts는 "AI 요약"에 넘길 원문이다.
# candidate_texts가 비어 있으면(예산/일정, 발주기관 - 이미 정제된 값) 프론트가
# AI 요약 버튼 자체를 안 그린다.
# =============================================================================
def _candidate_block(candidates: list[dict], empty_hint: str) -> dict:
    return {
        "type": "candidates",
        # start/end는 "원문 더 보기"(POST /api/expand)에서 이 후보 주변을 더 넓게
        # 다시 잘라주기 위한 원문 위치다. 후보를 만든 쪽(step24)이 이미 알고 있는
        # 값을 그대로 들고 오는 것 - 화면이 텍스트로 원문을 다시 찾지 않아도 된다.
        "items": [
            {
                "method": c["method"],
                "text": c["text"],
                "start": c.get("start"),
                "end": c.get("end"),
            }
            for c in candidates
        ],
        "empty_hint": empty_hint,
    }


def _qr_budget(row, text):
    return [{"type": "pre", "text": format_budget_schedule(row)}], []


def _qr_issuer(row, text):
    return [{"type": "text", "text": format_issuer(row)}], []


def _qr_form_list(row, text):
    forms = extract_form_list(text)
    block = {
        "type": "forms",
        "items": [{"label": f["label"], "before": f["before"], "after": f["after"]} for f in forms],
        "empty_hint": "번호 매겨진 서식이 본문에 없거나 다른 표기 방식일 수 있음",
    }
    return [block], [f"{f['before']} {f['label']} {f['after']}" for f in forms]


def _simple(extract: Callable, empty_hint: str, uses_row: bool = False, note: str | None = None):
    """정규식 후보 추출기 하나를 그대로 감싸는 공통 처리."""

    def handler(row, text):
        candidates = extract(row, text) if uses_row else extract(text)
        blocks = []
        if note:
            blocks.append({"type": "note", "text": note})
        blocks.append(_candidate_block(candidates, empty_hint))
        return blocks, [c["text"] for c in candidates]

    return handler


QUICK_REPLIES: list[dict] = [
    {"key": "budget_schedule", "label": "예산/일정", "question": "예산이랑 일정 알려줘", "handler": _qr_budget},
    {"key": "issuer", "label": "발주기관", "question": "발주기관이 어디야?", "handler": _qr_issuer},
    {"key": "form_list", "label": "첨부 서식 목록", "question": "첨부해야 하는 서식이 뭐가 있어?", "handler": _qr_form_list},
    {
        "key": "spec",
        "label": "제안서 작성 규정",
        "question": "제안서 작성 규정(분량/양식) 알려줘",
        "handler": _simple(extract_spec_candidates, "목차와 본문 구분이 어려운 문서일 수 있음"),
    },
    {
        "key": "subcontract",
        "label": "하도급/공동수급",
        "question": "하도급이나 공동수급 가능해?",
        "handler": _simple(extract_subcontract_candidates, "이 문서엔 관련 조항이 없을 수 있음"),
    },
    {
        "key": "purpose",
        "label": "사업목적 요약",
        "question": "이 사업 목적이 뭐야?",
        "handler": _simple(
            extract_purpose_candidates, "메타데이터 요약도 본문 후보도 없는 문서일 수 있음", uses_row=True
        ),
    },
    {
        "key": "contract_method",
        "label": "계약방식",
        "question": "계약방식/낙찰방식이 뭐야?",
        "handler": _simple(extract_contract_method_candidates, "이 문서엔 관련 조항이 없을 수 있음"),
    },
    {
        "key": "eligibility",
        "label": "참가자격",
        "question": "참가자격 제한이 있어?",
        "handler": _simple(extract_eligibility_candidates, "이 문서엔 관련 조항이 없을 수 있음"),
    },
    {
        "key": "bond",
        "label": "계약보증금",
        "question": "계약보증금 비율이 얼마야?",
        "handler": _simple(
            extract_bond_candidates,
            "실제로 이 문서 본문에 비율 숫자가 없는 경우가 많음(법령 인용/면제 문구만 있는 경우 등)",
        ),
    },
    {
        "key": "contact",
        "label": "문의처",
        "question": "문의처 연락처 알려줘",
        "handler": _simple(extract_contact_candidates, "본문에 연락처 정보가 아예 없는 문서일 수 있음"),
    },
    {
        "key": "eval_score",
        "label": "평가배점 (참고용)",
        "question": "평가배점 어떻게 돼?",
        "handler": _simple(
            extract_eval_score_candidates,
            "배점표 위치 자체를 못 찾았을 수 있음",
            note=(
                "배점표는 보통 표 형태라, 이 위치 힌트만으로는 실제 배점 숫자까지 "
                "정확히 뽑아내기 어려울 수 있어요."
            ),
        ),
    },
]

_QR_BY_KEY = {qr["key"]: qr for qr in QUICK_REPLIES}


def _get_row(doc_id: str):
    df = get_corpus()
    matched = df[df["doc_id"] == doc_id]
    if matched.empty:
        raise HTTPException(status_code=404, detail=f"그런 문서가 없습니다: {doc_id}")
    return matched.iloc[0]


def _run_quick_reply(doc_id: str, key: str):
    qr = _QR_BY_KEY.get(key)
    if qr is None:
        raise HTTPException(status_code=404, detail=f"그런 질문 키가 없습니다: {key}")
    row = _get_row(doc_id)
    text = row.get("text") or ""
    blocks, candidate_texts = qr["handler"](row, text)
    return qr, blocks, candidate_texts


# =============================================================================
# API
# =============================================================================
class QuickAnswerRequest(BaseModel):
    # doc_id는 한글/괄호/대괄호가 섞인 원본 파일명이라 URL 경로에 넣으면
    # 인코딩 사고가 나기 쉬워서, 전부 POST 본문으로 받는다.
    doc_id: str
    key: str
    model: str | None = None  # AI 요약에만 쓰임(정규식 후보 조회는 모델과 무관)


class ExpandRequest(BaseModel):
    doc_id: str
    key: str
    index: int
    width: int = 800  # 후보 앞뒤로 이만큼씩 더 보여준다


class ChatRequest(BaseModel):
    doc_id: str
    question: str
    model: str | None = None


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "")


def _masked_api_key() -> str | None:
    """키가 제대로 들어왔는지만 확인할 수 있게 앞뒤 몇 글자만 남긴다."""
    key = _api_key()
    if not key:
        return None
    if len(key) <= 12:
        return "***"
    return f"{key[:7]}...{key[-4:]} (길이 {len(key)})"


@app.get("/api/health")
def health():
    """컨테이너 헬스체크용 - 무거운 자원을 건드리지 않고 즉시 응답한다."""
    return {"status": "ok"}


@app.get("/api/status")
def status():
    client, client_error = get_openai_client()
    n_docs = None
    corpus_loaded = _corpus is not None
    if corpus_loaded:
        n_docs = len(_corpus)
    return {
        "corpus": {
            "loaded": corpus_loaded,
            "n_docs": n_docs,
            "mode": _corpus_mode,
            "note": _corpus_error,
            "path": str(MERGED_DOCS_PATH),
        },
        "embedding": {
            "loaded": _backend is not None,
            "name": getattr(_backend, "name", None),
            "error": _backend_error,
        },
        "openai": {"available": client is not None, "error": client_error},
        # 키가 왜 안 잡히는지 한눈에 보라고 넣어둔 진단 정보. 키 값 자체는 노출하지 않고
        # 앞뒤 몇 글자만 보여준다(제대로 된 키가 들어왔는지 확인하는 용도).
        "env": {
            "dotenv_path": str(DOTENV_PATH),
            "dotenv_found": DOTENV_FOUND,
            "python_dotenv_installed": PYTHON_DOTENV_INSTALLED,
            "api_key_detected": bool(_api_key()),
            "api_key_hint": _masked_api_key(),
        },
        "cached_indexes": list(_indexes.keys()),
    }


@app.get("/api/documents")
def documents(q: str = "", limit: int = 500):
    df = get_corpus()
    rows = df
    if q.strip():
        needle = q.strip()
        rows = df[df["doc_id"].str.contains(needle, case=False, na=False, regex=False)]
    out = []
    for _, r in rows.head(limit).iterrows():
        budget = r.get("사업_금액_정제")
        out.append(
            {
                "doc_id": r["doc_id"],
                "issuer": (str(r.get("발주 기관")) if pd.notna(r.get("발주 기관")) else None),
                "budget": (float(budget) if isinstance(budget, (int, float)) and pd.notna(budget) else None),
                "deadline": (
                    str(r.get("입찰참여마감일_정제")) if pd.notna(r.get("입찰참여마감일_정제")) else None
                ),
            }
        )
    return {"total": len(df), "matched": int(len(rows)), "items": out}


@app.get("/api/models")
def models():
    """화면 상단 드롭다운에 채울 생성 모델 목록."""
    return {"items": MODEL_CHOICES, "default": DEFAULT_GENERATION_MODEL}


@app.get("/api/quick-replies")
def quick_replies():
    return {
        "items": [{"key": qr["key"], "label": qr["label"], "question": qr["question"]} for qr in QUICK_REPLIES]
    }


@app.post("/api/quick-answer")
def quick_answer(req: QuickAnswerRequest):
    """버튼 하나에 대한 정규식 후보 답변. API 호출 없음(무료/즉시)."""
    qr, blocks, candidate_texts = _run_quick_reply(req.doc_id, req.key)
    return {
        "key": qr["key"],
        "question": qr["question"],
        "blocks": blocks,
        "can_summarize": bool(candidate_texts),
    }


@app.post("/api/expand")
def expand(req: ExpandRequest):
    """후보 하나의 원문 주변을 더 넓게 잘라 돌려준다(API 호출 없음, 즉시).

    후보는 원문에서 글자 수로 잘라낸 구간이라 아무리 경계를 다듬어도 문맥이
    더 필요한 경우가 있다. 그럴 때 LLM한테 "잘린 것 같아?"를 묻는 대신,
    원문이 이미 있으니 앞뒤로 더 잘라서 보여주면 된다 - 비용도 지연도 없고
    결과가 매번 같다.
    """
    row = _get_row(req.doc_id)
    text = row.get("text") or ""
    _qr, blocks, _cands = _run_quick_reply(req.doc_id, req.key)

    items = next((b["items"] for b in blocks if b["type"] == "candidates"), [])
    if not 0 <= req.index < len(items):
        raise HTTPException(status_code=404, detail="그런 후보가 없습니다.")
    item = items[req.index]
    if item.get("start") is None:
        raise HTTPException(status_code=400, detail="이 후보는 원문 위치 정보가 없어 확장할 수 없습니다.")

    width = max(200, min(req.width, 5000))
    start = max(0, item["start"] - width)
    end = min(len(text), item["end"] + width)
    return {
        "text": _snap_bounds(text, start, end),
        "at_doc_start": start == 0,
        "at_doc_end": end >= len(text),
        "width": width,
    }


@app.post("/api/ai-summary")
def ai_summary(req: QuickAnswerRequest):
    """정규식 후보를 chunk(가능하면 parent chunk)로 확장해 gpt-5-mini 요약을 받는다."""
    client, client_error = get_openai_client()
    if client is None:
        raise HTTPException(status_code=503, detail=client_error)
    model = _resolve_model(req.model)
    qr, _blocks, candidate_texts = _run_quick_reply(req.doc_id, req.key)
    if not candidate_texts:
        raise HTTPException(status_code=400, detail="요약할 원문 후보가 없습니다.")
    started = time.perf_counter()
    try:
        answer, matched = generate_quick_answer(
            client, req.doc_id, qr["question"], candidate_texts, model=model
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"모델 호출 중 오류: {e}")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if not answer:
        raise HTTPException(status_code=502, detail="모델이 답변을 생성하지 못했어요(네트워크/키 문제일 수 있음).")
    return {
        "answer": answer,
        "matched": matched,
        "note": ("문단 전체(더 넓은 문맥)를 근거로 답함" if matched else "원문 후보만 근거로 답함(더 넓은 문맥은 못 찾음)"),
        "model": model,
        "elapsed_ms": elapsed_ms,
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """선택한 문서 하나로 범위를 좁힌 자유 질문 - 벡터+BM25 하이브리드 검색 후 생성."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있어요.")
    model = _resolve_model(req.model)
    client, client_error = get_openai_client()
    if client is None:
        raise HTTPException(status_code=503, detail=client_error)
    _get_row(req.doc_id)  # 없는 문서면 여기서 404

    try:
        index = get_doc_index(req.doc_id)
        hits = index.hybrid_search(question, k=5, expand_to_parent=True)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"검색 중 오류: {e}")

    context = build_context(hits) if hits else "(이 문서에서 관련 내용을 찾지 못함)"
    started = time.perf_counter()
    try:
        answer = generate_answer(client, question, context, model=model)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"답변 생성 중 오류: {e}")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if not answer:
        raise HTTPException(status_code=502, detail="모델이 답변을 생성하지 못했어요(네트워크/키 문제일 수 있음).")
    return {
        "answer": answer,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "sources": [
            {
                "chunk_id": h.chunk_id,
                "doc_id": h.doc_id,
                "text": h.text,
                "matched_by": h.matched_by,
                "score": round(float(h.score), 4),
            }
            for h in hits
        ],
    }


# --- 정적 파일(웹 UI) --------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
