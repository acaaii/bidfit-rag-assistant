# 입찰메이트 (BidFit)

> 공공입찰 컨설턴트를 위한 근거 기반 RFP 의사결정 코파일럿

Repository: `bidfit-rag-assistant`

팀 프로젝트 문서: [입찰메이트 팀 Notion](https://app.notion.com/p/2-3-3c36e864c1d88044aa2afb76c6e24f59?source=copy_link)

입찰메이트는 공공입찰 컨설턴트가 고객사에 적합한 제안요청서(RFP)를 찾고, 참가 조건과 위험 요소를 원문 근거와 함께 검토하도록 돕는 RAG(Retrieval-Augmented Generation) 서비스다.

단순한 문서 질의응답을 넘어 `RFP 탐색 → 핵심 조건 확인 → 위험 검토 → 문서 비교 → 컨설팅 브리프 작성`으로 이어지는 실제 업무 흐름을 지원하는 것을 목표로 한다.

## 본인 기여 요약

이 저장소는 팀 프로젝트이며, 이 문서는 팀의 제품 기획을 담고 있다. 본인이 직접 담당한 부분은 데이터 전처리부터 검색·생성 평가, 성능 개선 실험, 서빙 앱과 배포 구성까지 파이프라인 전 구간이며, 전체 작업 기록과 실측 수치는 [PORTFOLIO.md](PORTFOLIO.md)에 정리되어 있다.

- 팀 공식 골든셋(111건) 기준 **recall@5 0.964, MRR 0.911**
- 동일 조건 비교에서 팀원 API 기준선 대비 **recall@5 +0.135, nDCG@10 +0.124**
- doc 단위 recall(0.972)과 chunk 단위 recall(0.611)의 괴리를 발견해 검색 파이프라인의 숨은 병목을 규명
- 리랭커 2종, 하이브리드 가중치 대안 등을 실측 후 기각 — "선호가 아니라 측정으로 결정" 원칙을 일관되게 적용
- FastAPI 서빙 앱 + Docker 배포 구성까지 완성

팀 전체의 저장소 구조, Git 협업 규칙, 역할 분담은 [docs/TEAM_COLLABORATION.md](docs/TEAM_COLLABORATION.md)에서 확인할 수 있다.

## 1. 문제 정의

입찰 컨설턴트는 다음 작업을 수작업으로 반복해야 한다.

- 많은 공고 중 고객사와 관련된 RFP 탐색
- 수십~수백 페이지 문서에서 핵심 조건 추출
- 참가 자격, 공동수급 조건, 필수 제출서류 확인
- 지체상금, 하자보수, 손해배상 등 위험 조항 탐지
- 여러 RFP 비교 및 고객사 전달용 검토자료 작성

문서가 많고 길어질수록 검토 시간이 증가하고 중요한 조건을 놓칠 위험도 커진다. 입찰메이트는 제공받은 약 100개의 RFP와 메타데이터를 검색하고, 검색된 원문만을 근거로 답변하여 검토 시간을 줄인다.

## 2. 핵심 사용자 흐름

```text
고객사 조건 또는 자연어 질문 입력
    → 적합한 RFP 후보 탐색
    → 사업 개요와 핵심 조건 요약
    → 참가 자격 및 위험 조항 검토
    → 후속 질문과 원문 근거 확인
    → 여러 RFP 비교
    → 컨설팅 브리프 및 체크리스트 생성
    → 입찰 참여 여부 의사결정 지원
```

## 3. 핵심 기능

| 기능 | 설명 |
| --- | --- |
| RFP 탐색 | 사업 분야, 금액, 마감일, 발주기관, 지역 등 자연어 조건으로 보유 RFP 검색 |
| 입찰 검토 카드 | 사업 개요, 참가 자격, 제출서류, 평가 방식, 위험 요소를 정형 형식으로 제공 |
| 근거 기반 Q&A | 답변과 함께 문서명, 페이지, 근거 문장을 제시 |
| 멀티턴 대화 | 선택한 RFP, 고객사 조건, 이전 질문의 맥락을 유지 |
| 리스크 조항 탐지 | 지체상금, 하자보수, 손해배상, 계약해지, 재위탁 제한 등 검토 필요 조항 제시 |
| 다중 문서 비교 | 여러 RFP를 동일 기준으로 비교하고 각 값의 문서 근거를 표시 |
| 고객사 기반 매칭 | 적합, 검토 필요, 부적합 가능성, 판단 불가의 설명 가능한 단계로 판정 |
| 컨설팅 브리프 | 고객 미팅에 활용할 수 있는 1페이지 검토 요약 생성 |

법률적 입찰 가능 여부를 확정하지 않으며, 확인이 필요한 조항과 원문 근거를 제공하는 도구로 범위를 제한한다.

## 4. MVP 범위

### 필수 구현

- PDF/HWP 텍스트, 표, 페이지 정보, 메타데이터 추출
- 문서 정제, 청킹, 임베딩 및 벡터 DB 저장
- 등록된 RFP 대상 자연어 검색
- 단일 RFP 기반 질의응답
- 문서명, 페이지, 근거 문장 표시
- 이전 질문을 기억하는 후속 질문
- 원문에 없는 질문에 대한 기권 처리
- Golden Set 기반 검색 및 답변 평가

### 확장 후보

- 30초 입찰 검토 카드
- 기본적인 다중 문서 비교
- 고객사 프로필 기반 추천 및 매칭
- 리스크 조항 자동 탐지
- 검색 결과 재정렬(Reranking)
- 근거 위치 하이라이트 및 답변 신뢰도 표시
- API 모델과 GCP 로컬 모델 비교

### 제외 범위

- 나라장터 실시간 크롤링과 신규 공고 알림
- 법률적 입찰 가능 여부 확정
- 실제 입찰서 자동 제출
- 복잡한 운영 스케줄링 인프라

## 5. RAG 파이프라인

```text
PDF/HWP + 메타데이터
    → 텍스트·표·페이지 정보 추출
    → 정제 및 문서 구조 기반 청킹
    → 임베딩 생성 및 벡터 DB 저장
    → 사용자 질문 분석
    → 메타데이터 필터 + 문서 검색
    → Dense / BM25 / Hybrid Retrieval
    → 필요 시 Reranking
    → 질문 유형별 답변 생성
    → 답변 + 문서명 + 페이지 + 근거 문장
```

### 답변 생성 원칙

- Retrieval이 제공한 컨텍스트만 사용한다.
- 문서에서 확인되지 않은 사실은 생성하지 않는다.
- 사실과 해석을 분리한다.
- 수치, 일정, 자격 조건에는 출처를 표시한다.
- 불확실한 내용은 `확인되지 않음` 또는 `추가 확인 필요`로 답변한다.

## 6. 모델 실행 시나리오

| 구분 | 시나리오 B: API 베이스라인 | 시나리오 A: GCP 로컬 모델 |
| --- | --- | --- |
| 목적 | 우선 구현 및 기준 성능 확보 | B안 완성 후 비교 실험 |
| 생성 모델 | OpenAI API 후보 모델 비교 | Qwen, Llama, Gemma 계열 후보 비교 |
| 임베딩 | `text-embedding-3-small` 후보 | 실험 결과에 따라 선정 |
| 벡터 저장소 | FAISS 또는 Chroma | FAISS 또는 Chroma |
| 주요 평가 | 정확도, 속도, 질문당 비용 | 한국어 품질, 근거 충실성, 속도, GPU 메모리 |

최종 모델은 선호가 아니라 동일한 Golden Set에서 측정한 정확도, 속도, 비용을 기준으로 선택한다.

### 현재 통합 모델

현재 구현은 KURE-v1 + BM25 하이브리드 검색과 Parent-Child 컨텍스트 확장을
공통 기반으로 사용하고, 생성기만 GPT-5 mini 또는 Local Qwen(vLLM/Ollama)으로
교체한다. 검색 결과는 해시 기반 근거 레코드로 저장하며 답변이 검색되지 않은
문서를 인용했는지도 검사한다.

```bash
# GPT-5 mini API 기준선
python scripts/run_integrated_model.py --provider openai --query "사업 예산은?"

# GCP VM의 Local Qwen
python scripts/run_integrated_model.py --provider vllm --query "사업 예산은?"
```

세부 실행법, 최신 원본 브랜치 tip, 기능별 반영 여부는
[`docs/integrated-pipeline.md`](docs/integrated-pipeline.md)에서 확인할 수 있다.

## 7. 평가 계획

Golden Set에는 기본 사실, 참가 자격, 표 정보, 위험 조항, 문서 비교, 후속 질문, 답변 불가 유형을 고르게 포함한다.

| 구분 | 평가 항목 |
| --- | --- |
| Retrieval | Recall@k, 정답 문서 Top-3 성공률, 정답 페이지·청크 검색률, 필터 정확도 |
| Generation | 답변 정확성, 근거 충실성, 환각 여부, 페이지 인용 정확도, 기권 정확도 |
| 운영 | 응답 시간, 질문당 API 비용, GPU 메모리, 모델별 추론 속도 |
| 업무 효과 | 핵심 필드 추출 정확도, 수작업 검토 대비 절약 시간 |

## 8. 기술 스택

초기 베이스라인 기준이며 실험 결과에 따라 변경될 수 있다.

| 구분 | 기술 |
| --- | --- |
| Language | Python |
| Document Processing | PyMuPDF, HWP parser, pandas |
| Embedding | OpenAI Embedding 또는 Hugging Face Embedding |
| Retrieval | FAISS/Chroma, Dense, BM25, Hybrid Retrieval |
| Generation | OpenAI API, Hugging Face Transformers |
| Demo UI | Streamlit |
| Infrastructure | GCP VM, NVIDIA L4, JupyterHub |

## 9. 프로젝트 구조

```text
.
├── app/                  # 데모 애플리케이션
├── configs/              # 모델 및 실험 설정
├── data/                 # 로컬 데이터, Git 업로드 금지
├── docs/                 # 기획서, 보고서, 회의 기록
├── notebooks/            # 데이터 탐색 및 실험 노트북
├── results/              # 공개 가능한 평가 결과
├── src/
│   ├── data_processing/  # 문서 추출, 정제, 청킹
│   ├── retrieval/        # 임베딩, 인덱싱, 검색
│   ├── generation/       # 프롬프트 및 답변 생성
│   └── evaluation/       # 검색·생성 성능 평가
├── scripts/              # 파이프라인·통합 모델·평가 실행 CLI
└── tests/                # 테스트 코드
```

## 10. 보안 및 데이터 관리

- 원본 RFP와 외부 공유가 제한된 데이터는 GitHub에 업로드하지 않는다.
- API Key와 비밀번호는 `.env`에 저장하고 커밋하지 않는다.
- `.env.example`에는 변수 이름만 기록하고 실제 값은 작성하지 않는다.
- 벡터 인덱스와 임베딩에는 원문이 포함될 수 있으므로 공개 여부를 확인한다.
- 모델 가중치, 캐시, 대용량 결과물은 Git이 아닌 별도 저장소를 사용한다.
- 공개 가능한 코드, 평가 결과, 2차 가공 자료만 저장소에서 공유한다.

## 11. 진행 상태

- [x] GitHub 저장소 생성
- [x] GCP VM 및 JupyterHub 환경 구축
- [x] 프로젝트 기획 및 MVP 범위 정리
- [x] README와 Git 협업 규칙 작성
- [x] 팀원 역할 확정
- [x] 데이터 구조 분석 및 전처리 (`feat/rag-pipeline-and-eval`, `src/data_processing`)
- [x] 공통 RAG 베이스라인 구현 (`feat/rag-pipeline-and-eval`, `src/retrieval` + `src/generation`,
      시나리오 B: API 임베딩 비교 + KURE-v1/BM25 hybrid + gpt-5-mini)
- [x] Golden Set 구축 (`feat/rag-pipeline-and-eval`, 공식 111건 + golden-set-v3-share 공유 lane 연동)
- [x] 검색 및 생성 성능 개선 실험 (`feat/rag-pipeline-and-eval`, Parent-Child·임베딩 A/B·리랭커·
      가중치 튜닝·프롬프트 개선 — 상세는 `docs/rag-pipeline-and-eval-summary.md`)
- [x] 서빙 화면 프로토타입 구현 (Streamlit 채팅형 UI)
- [x] 서빙 앱(FastAPI + 웹 UI) + Docker 구성
- [ ] API 모델과 GCP 로컬 모델 비교 (시나리오 A는 아직 미착수)
- [ ] 데모 및 최종 보고서 완성
