# 팀 협업 규칙 및 역할 분담

> 원래 루트 README.md의 "Git 협업 규칙"·"역할 분담" 절을 분리한 문서입니다. 프로젝트 개요·기술 스택은 [README](../README.md)를, 본인 기여 내역은 [PORTFOLIO.md](../PORTFOLIO.md)를 참고하세요.

## 언어 및 이름 규칙

- 코드 식별자, 파일명, 폴더명, 브랜치명은 **영어**를 사용합니다.
- README, 문서, PR 설명, 커밋 요약은 **한국어**를 사용합니다.
- Python 파일과 폴더는 `snake_case`를 사용합니다.
- 브랜치는 `<type>/<kebab-case-description>` 형식을 사용합니다.
- 경로에는 공백, 한글, 특수문자를 사용하지 않습니다.
- 노트북은 `YYYYMMDD_topic_owner.ipynb` 형식으로 작성합니다.

예시:

```text
src/data_processing/hwp_parser.py
notebooks/20260825_chunking_baseline_kongseok.ipynb
feat/retrieval-baseline
fix/hwp-page-mapping
experiment/chunk-size
```

## 브랜치 규칙

| 브랜치 | 용도 |
| --- | --- |
| `main` | 최종 검증을 마친 안정 버전 |
| `develop` | 팀원별 작업을 합치고 전체 파이프라인을 검증하는 통합 브랜치 |
| `feat/*` | 새로운 기능 구현 |
| `fix/*` | 오류 수정 |
| `experiment/*` | 모델, 청킹, 임베딩, 검색 비교 실험 |
| `docs/*` | README, 보고서, 문서 수정 |
| `chore/*` | 설정, 의존성, 폴더 구조 등 유지보수 |

`main`과 `develop`에는 직접 push하지 않습니다. 각 팀원은 자신의 작업 브랜치에서 기능 단위 테스트를 마친 뒤 `develop`을 대상으로 Pull Request를 생성합니다. `develop`에서는 브랜치를 하나씩 병합하며 통합 테스트를 수행하고, 최종 검증이 끝난 시점에만 `develop`을 `main`으로 병합합니다.

```text
팀원별 작업 브랜치 → develop → 통합 테스트 → main
```

새 작업 브랜치는 최신 `develop`에서 생성합니다.

```bash
git switch develop
git pull origin develop
git switch -c feat/retrieval-baseline
```

## 커밋 메시지 규칙

형식은 `<type>: <한국어 요약>`으로 통일합니다. 타입은 소문자, 콜론 뒤에는 공백 한 칸을 사용하고 문장 끝에 마침표를 붙이지 않습니다.

| 타입 | 사용 시점 | 예시 |
| --- | --- | --- |
| `feat` | 새로운 기능 추가 | `feat: BM25 검색 기능 추가` |
| `fix` | 버그 또는 잘못된 동작 수정 | `fix: HWP 페이지 번호 매핑 오류 수정` |
| `update` | 데이터, 설정, 모델 후보, 기존 내용 갱신 | `update: 임베딩 모델 비교 결과 갱신` |
| `experiment` | 실험 코드나 결과 추가 | `experiment: 청크 크기별 Recall@5 비교` |
| `refactor` | 기능 변화 없는 코드 구조 개선 | `refactor: 검색 파이프라인 모듈 분리` |
| `perf` | 속도 또는 메모리 성능 개선 | `perf: 임베딩 배치 처리 속도 개선` |
| `test` | 테스트 추가 또는 수정 | `test: 답변 기권 케이스 추가` |
| `docs` | README, 주석, 보고서 수정 | `docs: 실행 방법과 협업 규칙 추가` |
| `style` | 포맷팅, 공백, 이름 등 비기능 수정 | `style: ruff 기준으로 코드 포맷 정리` |
| `chore` | 패키지, 설정, 기타 유지보수 | `chore: 개발 의존성 파일 추가` |
| `build` | 빌드 또는 패키징 설정 변경 | `build: Docker 이미지 설정 추가` |
| `ci` | 자동화 워크플로 변경 | `ci: pull request 테스트 추가` |
| `revert` | 이전 변경 되돌리기 | `revert: 하이브리드 검색 적용 취소` |

## 커밋 및 Pull Request 원칙

- 하나의 커밋에는 하나의 논리적 변경만 포함합니다.
- 하나의 Pull Request에는 하나의 목적만 포함합니다.
- PR 제목도 커밋과 같은 형식을 사용합니다.
- PR 본문에는 작업 내용, 확인 방법, 실험 결과, 관련 이슈를 기록합니다.
- 모델·청킹·검색 실험은 설정값과 평가 지표를 함께 남깁니다.
- 병합 전 최소 한 명의 팀원에게 리뷰를 요청합니다.

## 버전 태그 규칙

| 버전 | 기준 예시 |
| --- | --- |
| `v0.1.0` | API 기반 공통 베이스라인 완성 |
| `v0.2.0` | 검색 고도화 및 평가 반영 |
| `v0.3.0` | GCP 로컬 모델 비교안 완성 |
| `v1.0.0` | 최종 발표 및 제출 버전 |

## 역할 분담

| 담당 | 주요 업무 |
| --- | --- |
| PM·통합 | 일정 관리, 요구사항 정리, 통합, 발표자료 |
| 데이터·청킹 | 문서 추출, 정제, 메타데이터 연결, 청킹 실험 |
| Retrieval | 임베딩, 벡터 DB, 검색, 필터링, 검색 평가 |
| Generation·UI | 프롬프트, 답변 생성, 출처 표시, 데모 UI |

