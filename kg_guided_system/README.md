# KG 기반 공공서비스 자격 상담

```
문장 ─① 프로필 추출(LLM)─▶ 프로필 JSON ─② 개념 연결(규칙·정확·n-gram·LLM 확인)─▶ KG 개념
     ─③ 판정기(KG 규칙)─▶ 적합 / 확인 필요 / 부적합 + 근거 경로 ─▶ 질문 관련 순위 · 되물을 정보
```

`../public_service_kg_final_20261011/output/kg.ttl`(또는 `kg_base.ttl`)을 쓴다.
LLM 은 해석(①②)만 하고, 자격은 판정기가 KG 로 결정한다.

## 문장으로 묻기 (①②③)

```bash
# 연구실 서버: vLLM 을 띄운 뒤 (기본 http://localhost:8000/v1, 모델 Qwen/Qwen3-32B)
python -m judge.ask "신당동 사는 22살 대학생인데 기초수급자예요. 장애는 없어요. 월세 지원 있나요?"
python scripts/eval_understanding.py                 # 추출·연결 평가 (문장 15개)
# GPU 없이: 한 번 돌린 LLM 호출은 cache/llm_cache.jsonl 에 남으므로 그대로 재생
LLM_CACHE_MODE=only python -m judge.ask "..."
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
