# KG 기반 공공서비스 자격 상담

```
문장 ─① 프로필 추출(LLM)─▶ 프로필 JSON ─② 개념 연결(규칙·정확·n-gram·LLM 확인)─▶ KG 개념
     ─③ 판정기(KG 규칙)─▶ 적합 / 확인 필요 / 부적합 + 근거 경로 ─▶ 질문 관련 순위 · 되물을 정보
     ─⑥ 답변 생성(LLM, 근거 [S#] 만 사용)─▶ 근거 검사(없는 사업명·숫자·인용 불일치)
```

`../public_service_kg_final_20261011/output/kg.ttl`(또는 `kg_base.ttl`)을 쓴다.
LLM 은 해석(①②)과 문장 쓰기(⑥)만 하고, 자격은 판정기가 KG 로 결정한다.

## 비교 실험 (KG vs KG-base vs 텍스트 RAG)

```bash
python scripts/compare_systems.py      # 질문 15개 × 시스템 3개 → results/compare.jsonl, compare.md, pool_for_annotation.csv
# pool_for_annotation.csv 의 gold 열에 적합 / 확인 필요 / 부적합 을 채운 뒤 (원문 target_text 를 보고)
python scripts/score_systems.py        # P@5, P@10, 적합 정밀도, 위험한 오판, 적합 재현율, 답변 경고율, 호출 수, 시간
```

| 시스템 | 검색·판정 | 답변 | LLM 호출 (질문당) |
|---|---|---|---|
| KG | ①추출 → ②연결 → ③판정기(`kg.ttl`) | ⑥ 판정 근거로 생성 | 추출 1 + 개념 확인 2~4 + 답변 1 |
| KG-base | 같은 파이프라인, 수업 기본 KG `kg_base.ttl` | 같음 | 같음 |
| RAG (B1) | BM25 상위 20건 → LLM 이 원문을 읽고 5건씩 판정 | 같은 생성기, 근거는 원문 | 판정 4 + 답변 1 |

- RAG 는 KG 의 구조(조건 관계, 상위 개념, 수치, 지역 계층)는 쓰지 않고 KG 에 보존된 원문 텍스트만 쓴다.
- 정답은 풀링으로 만든다: 세 시스템이 낸 상위 10건의 합집합만 사람이 판정한다 → KG 로 정답을 만드는 순환을 피한다.
- 위험한 오판 = 시스템은 '적합' 이라 했는데 정답은 '부적합'. 상담 시스템에서 가장 피해야 할 오류다.

| 파일 | 하는 일 |
|---|---|
| `judge/answer.py` | 근거 묶음(질문 관련 사업 7 + 그 밖의 적합 3), 답변 프롬프트, 되물을 질문, 근거 검사, LLM 없을 때 틀 답변 |
| `judge/rag.py` | B1: BM25(단어 + 글자 2-gram), LLM 원문 판정, 같은 답변 생성기 |
| `judge/systems.py` | 두 시스템을 같은 형식(`SystemOutput`: 순위 목록·답변·호출 수·시간)으로 실행 |
| `scripts/compare_systems.py`, `scripts/score_systems.py` | 비교 실행·풀 생성, 채점 |

## 문장으로 묻기 (①②③⑥)

```bash
# 연구실 서버: vLLM 을 띄운 뒤 (기본 http://localhost:8000/v1, 모델 Qwen/Qwen3-32B)
python -m judge.ask "신당동 사는 22살 대학생인데 기초수급자예요. 장애는 없어요. 월세 지원 있나요?"
python scripts/eval_understanding.py                 # 추출·연결 평가 (문장 15개)
# GPU 없이: 한 번 돌린 LLM 호출은 cache/llm_cache.jsonl 에 남으므로 그대로 재생
LLM_CACHE_MODE=only python -m judge.ask "..."
python -m judge.ask "..." --no-answer                # 답변 생성 없이 판정 결과만
```

환경 변수: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`(외부 API), `LLM_CACHE_MODE`(use / only / off).

| 단계 | 파일 | 하는 일 |
|---|---|---|
| LLM | `judge/llm.py` | OpenAI 호환 클라이언트(vLLM·외부 API), 호출 캐시, 1차 자유 생성 → 실패 시 2차 JSON 스키마 강제 |
| ① 추출 | `judge/extract.py` | KG 에서 많이 쓰이는 범주 120개를 프롬프트 어휘로 제시. 나이(출생연도)·소득 → 2026 기준중위소득 % 환산은 코드가 계산 |
| ② 연결 | `judge/linker.py` | 규칙(청년·노인·여성·○○구 거주자·국민) → 정확 일치 → 글자 n-gram 후보 → LLM 이 '확실히 속하는' 개념만 선택 |
| 묶음 | `judge/pipeline.py` | `Consultant.consult(문장)`. 질문 키워드·분야·지원유형이 맞는 사업을 먼저 보이고, 그 안에서 판정 단계 순 |
| 실행 | `judge/ask.py` | 명령줄 상담 |

설계에서 정한 것
- **연결 방향**: 사람이 X 면 X 의 상위 개념에도 속하지만 하위 개념은 아니다. 'X 아님'은 반대. LLM 확인 프롬프트가 이 방향을 지킨다.
- **동의 개념**: KG 에는 같은 대상이 여러 개념으로 나뉘어 있다(한부모 11건 / 한부모가족 69건 / 한부모가정). 정확 일치가 있어도 유사 후보 중 같은 대상을 LLM 이 더 고른다.
- **청소년은 규칙에서 뺐다**: 법(9~24세)대로 22세를 청소년으로 넣으면 10대 대상 사업이 잘못 적합이 된다.
- **개인 사용자는 가구 단위 사업(ut_가구)도 본다.**
- **LLM 없이도 동작**: `--no-llm-link` 이면 정확 일치 + n-gram 0.8 이상만 쓴다 (비교 실험용).

## 첫 실제 실행(2026-11-19) 후 고친 것

`results/RUN_REPORT.md`(연구실 GPU, Qwen3-32B FP8)의 문제 사례를 `tests/test_fixes_20261119.py`로 재현하고 고쳤다.

| 문제 | 고친 것 |
|---|---|
| 개념 연결이 방향을 어김 (기초생활수급자 → 의료급여 수급자, 소상공인 → 강서구 소상공인, ¬기초생활수급자 → ¬기초연금 수급자) | `linker.enforce_direction`: 라벨 모양(조건이 붙은 범주, 'X/Y' 결합 범주)과 KG 계층으로 방향을 코드에서 강제. 뺀 것은 `Link.rejected` 에 이유와 함께 남김 |
| 수급자 제외 사업을 기초수급자에게 '받을 수 있어요' | `data/kg_patches.ttl` (원본 KG 는 그대로, 누락 관계만 덧붙임): 생계·의료·주거·교육급여 수급자 ≡ 기초생활수급자 등. 제외 조건이 남은 적합 사업은 `적합(제외 조건 확인)` 으로 근거에 표시 |
| 추출이 암시된 범주를 놓치고(구직 중, 혼자 삶, 결혼 1년 …) 사실을 지어냄(기초연금 → 기초생활수급자, 딸의 학년 → 본인) | 추출 프롬프트에 암시 예시와 금지 규칙. 출생연도가 있으면 나이는 코드가 계산(모델 값 무시), 임산부 → 여성 |
| 일반 범주(국민·일반인·지역주민)만으로 충족된 사업이 적합 상위에 옴 | 판정 조건에 충족 근거(`Condition.via`) 기록 → 구체적 범주로 충족한 사업 먼저, 답변 근거의 '그 밖의 적합' 에서는 제외 |
| 답변에 `[되물을 질문]` 태그 노출, 같은 사업 중복, 조건부 사업 단정 | 프롬프트 정리 + 태그 제거 후처리 + 같은 사업명 하나로 + 근거 검사에 '조건부 사업 단정' 추가 |
| 개인에게 법인·기관 범주를 되물음 | `missing_info(skip=...)`: 개인·가구 사용자에게는 법인/시설/단체·기업 계열 범주를 묻지 않음 |
| 연결 채점이 방향 오류를 못 잡음 | `eval_understanding.py` 에 개념 단위·방향 반영 정밀도, 방향 규칙으로 뺀 수 |
| 7인 가구 기준중위소득 외삽 | 고시 값 9,515,150원. 8인 이상은 (7인 − 6인) 차이를 더함 (고시문 확인 필요) |
| 질문 관련 판정이 키워드 없이도 참이 될 수 있었음 | 키워드가 있으면 사업명·지원내용에 실제로 있어야 관련 |

평가 문장: `data/understanding_eval.jsonl`(15개)은 위 수정을 할 때 본 **개발용**이다.
`data/understanding_heldout.jsonl`(10개)은 수정할 때 보지 않은 **확인용**이므로 발표에는 두 결과를 따로 보고한다.

## 판정기만 쓰기 (③)

```bash
pip install -r requirements.txt          # pyoxigraph, pytest (conda pskg 환경이면 이미 있음)
python -m judge.cli --age 22 --region 중구 --groups 대학생 기초생활수급자 --today 2026-11-18
python -m judge.cli --age 22 --region 중구 --income 30 --groups 대학생 대학생/대학원생 무주택세대 --closed --targeted-only   # = Q0
python -m pytest -q                      # 전체 테스트 (GPU·네트워크 불필요, 가짜 LLM 사용)
```

```python
from judge import Judge, Profile, Tier, load_kg
J = Judge(load_kg("../public_service_kg_final_20261011/output/kg.ttl"))
res = J.run(Profile(age=22, region="중구", groups=["대학생", "기초생활수급자"], not_groups=["장애인"]))
for j in res.by_tier(Tier.ELIGIBLE)[:5]:
    print(j.service.title, [(c.text, c.status.value, c.evidence) for c in j.conditions])
```

## 구성

| 파일 | 내용 |
|---|---|
| `judge/kg_index.py` | TTL → 파이썬 색인. `skos:broader*`, `kg:partOf*` 폐포를 미리 계산 |
| `judge/judge.py` | `Profile`, `Judge.run`, 신청기한 판정 `apply_status`, 미확인 정보 집계 `missing_info` |
| `judge/cli.py` | 명령줄 실행 |
| `scripts/make_sparql_fixtures.py` | 기존 SPARQL(Q0·Q1·Q5)을 실행해 검증용 정답 생성 |
| `tests/test_sparql_compat.py` | 실제 KG 두 개에서 판정기 = SPARQL (서비스 ID 단위) |
| `tests/test_judge_unit.py` | 작은 KG(`tests/fixtures/mini.ttl`)로 규칙별 확인 |

## 판정 규칙

설계서 자격 판정 규칙 1~6(`queries/q1`)과 같다. 사람이 범주 X에 해당하면 X의 상위 개념에도 해당한다.

| 조건 | 충족 | 불충족 | 모름 (open 모드) |
|---|---|---|---|
| requires | 해당(상위 개념 포함) | `not_groups`(하위 개념 포함)에 있음 | 그 밖 |
| OR 묶음 | 하나 이상 충족 | 모두 불충족 | 그 밖 |
| excludes | — | 해당하면 부적합 | 판정에 영향 없이 `caveats`로 표시 |
| 연령·소득·성별 | 범위 안 | 범위 밖 (`incomeMinPct`는 초과) | 프로필에 없음 |
| 지역 | 서비스 지역이 사는 곳의 상위(`partOf*`) | 다른 지역 | 구를 모름 (전국 사업은 충족) |
| 사용자 유형 | 기본 `개인` | | |

- **open**(기본): 하나라도 불충족이면 부적합, 아니고 모름이 있으면 확인 필요, 모두 충족이면 적합.
- **closed**: 기존 SPARQL과 같은 닫힌 세계. 밝히지 않은 범주는 불충족, 밝히지 않은 수치는 검사 안 함.
- 순위: 판정 단계 → 모르는 조건 수 → 밝힌 범주로 충족한 조건 수 → 우대 수 → 대상이 정해진 사업 → 가까운 지역.
- `receiving=["근로장려금"]`처럼 이미 받는 서비스를 주면 `kg:recipientOf`로 그 수혜자 범주를 더한다.

## 검증 결과

| 질의 | SPARQL (pyoxigraph) | 판정기 | 일치 |
|---|---|---|---|
| Q0 kg_base / kg | 77 / 41건, 9초 / 69초 | 77 / 41건 | 서비스 ID 완전 일치 |
| Q1 kg_base / kg | 54 / 69건, 8초 / 67초 | 54 / 69건 | 서비스 ID 완전 일치 |
| Q5 kg_base / kg | 8 / 8건 | 8 / 8건 | 서비스 ID 완전 일치 |

판정기는 KG 로드 약 0.25초(한 번), 2,461건 판정 약 25ms.

## 알려진 한계 (KG 쪽)

- `대한민국 국민`, `가구`, `일반인` 같은 일반어가 requires로 남아 있어 대부분 사람에게 '확인 필요'가 된다.
- 범주와 수치의 관계는 모른다. 예: 기초생활수급자인데 '중위소득 50% 이하'를 따로 묻는다.
- `kg.ttl`의 AND/OR 구조 오류(표본 정답률 62.5%)가 그대로 판정에 반영된다. 원문 검증 단계에서 보완할 것.
- 같은 대상인데 상위 관계가 없는 개념이 있다. 예: 기초생활수급자인 사람에게 「희망두배 청년통장」의 제외 조건
  「생계·의료·주거·교육급여 수급자」가 '모름'(주의 사항)으로만 나온다.
- 2026 기준중위소득 표(`extract.py`)는 고시문과 한 번 대조할 것.
