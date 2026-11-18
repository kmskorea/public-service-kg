# KG 기반 공공서비스 자격 상담 — 판정기

`../public_service_kg_final_20261011/output/kg.ttl`(또는 `kg_base.ttl`)을 읽어
사람의 프로필로 서비스 2,461건을 **적합 / 확인 필요 / 부적합**으로 나누고, 판정마다 근거(개념 경로)를 남긴다.
LLM 단계(프로필 추출·개념 연결·답변 생성)는 이 판정기의 결과만 사용한다.

## 실행

```bash
pip install -r requirements.txt          # pyoxigraph, pytest (conda pskg 환경이면 이미 있음)
python -m judge.cli --age 22 --region 중구 --groups 대학생 기초생활수급자 --today 2026-11-18
python -m judge.cli --age 22 --region 중구 --income 30 --groups 대학생 대학생/대학원생 무주택세대 --closed --targeted-only   # = Q0
python -m pytest -q                      # 30개 테스트
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
