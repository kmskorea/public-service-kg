# 대한민국 공공서비스 KG — 온톨로지 설계 v0.2 구현

팀 설계서(`data/team/ontology_design.md`)와 팀 데이터 `services.csv`(2,461건)로 만든 RDF 지식그래프다. 팀이 만든 기본 KG(`data/team/kg_base_team.ttl`)와 같은 IRI·라벨 체계를 쓰고, 그 위에 설계서 4·5장(원문 LLM 추출과 합치기)을 더했다.

- 형식: RDF Turtle. rdflib로 구축하고 서버 없이 파일로 관리한다.
- 질의: pyoxigraph 메모리 스토어로 SPARQL을 실행한다.
- 이전 버전: Neo4j 청년 KG(v1)는 `archive/neo4j_v1/`, 스냅샷에서 직접 고른 2,577건 버전은 `archive/snapshot_2577/`.

## 실행

```bash
./scripts/run_all.sh             # 대상 데이터 → 기본 KG → 합치기 → 질의 (저장된 LLM 결과 사용)
./scripts/run_all.sh --llm       # Qwen 추출까지 다시 (GPU 1번, 약 1시간)
./scripts/run_all.sh --collect   # API 에서 새 스냅샷 수집부터
```

| 단계 | 스크립트 | 출력 | 설계서 |
|---|---|---|---|
| 수집 | `scripts/collect.py` | `data/raw/*_<날짜>.json` | 1장 |
| 대상 데이터 | `scripts/build_target.py` | 팀 `services.csv` 그대로 + 전체 필드를 붙인 `services.jsonl` | 1장 |
| 기본 KG | `scripts/build_kg.py` (+ `kg_common.py`, `deadline_rules.py`) | `output/kg_base.ttl` | 2·3장, 5.1 |
| LLM 추출 | `scripts/llm_extract.py` | `data/llm/deadline.jsonl`, `target.jsonl`, `canon.json` | 4장 |
| 합치기 | `scripts/resolve.py` | `output/kg.ttl`, `output/resolution_log.csv` | 5장 |
| 질의 | `queries/*.rq`, `scripts/run_queries.py` | `output/query_results.md` | 6.4 |
| 개요 데이터 | `scripts/export_overview.py` | `data/overview/*.json` (개요 대시보드용) | |

- 환경
  - 대상 데이터·기본 KG·합치기·질의: conda `pskg` (rdflib, pyoxigraph)
  - LLM 추출: conda `llm` (vLLM 0.31, Qwen3-32B)
  - 수집: 시스템 `python3`
- CPSV-AP 대응표: `docs/cpsv_mapping.md`
- 비밀값: `.env` (`DATA_GO_KR_KEY`)

## 웹에서 보기 (Neo4j 브라우저, 보기용 사본)

원본은 `.ttl` 파일이다. 그래프를 브라우저로 보고 싶을 때만 Neo4j에 사본을 싣는다.

```bash
export JAVA_HOME=$HOME/anaconda3/envs/pskg/lib/jvm
tools/neo4j-community-5.26.0/bin/neo4j start                              # stop / status
~/anaconda3/envs/pskg/bin/python scripts/ttl_to_neo4j.py                  # kg.ttl → Neo4j (kg_base.ttl 도 가능)
```

- VSCode **포트** 탭에서 7474와 7687을 포워딩한 뒤 http://localhost:7474 에 접속한다.
- 로그인: Connect URL `neo4j://localhost:7687`, 계정 `neo4j`, 비밀번호는 `.env`의 `NEO4J_PASSWORD`.
- 노드마다 라벨은 하나다: `Service`, `Organization`, `Region`, `RequirementGroup`, 그리고 개념은 스킴 이름 `TargetGroup`, `Field`, `SupportType`, `UserType`, `OrgType`, `Dimension`. 브라우저가 라벨별로 색을 다르게 칠한다. 서비스 노드의 `id`는 서비스ID다.
- 관계 이름은 온톨로지와 같다(`requires`, `anyOf`, `excludes` …). `skos:broader`는 `BROADER`로 싣는다.
- 이름 검색: `CALL db.index.fulltext.queryNodes('resource_name', '청년월세') YIELD node RETURN node`

## 팀 기본 KG와의 관계

같은 설계서를 따로 구현한 두 기본 KG를 트리플 단위로 비교했다(공백·줄바꿈 표기 차이는 무시).

- 팀 `kg_base_team.ttl` 트리플 37,486개 중 **37,069개(98.9%)가 이 저장소의 `kg_base.ttl`에 그대로 있다.**
- 남은 417개는 모두 신청기한이다. 이쪽 규칙 파서가 더 많이 풀었거나(UNKNOWN 518 → 161) 더 정확히 읽었다. 예: 「2026. 3. 25.(수) ~ 4. 7.(화)」의 종료일을 팀은 3-25, 이쪽은 4-07.
- 이쪽에만 있는 것: 지원내용 원문(`benefitText`), 정부24 URL(`detailUrl`), JA 코드(`jaCode`), CPSV-AP 상위 클래스·속성.
- 맞춘 규칙: 네임스페이스 `kg.example.org`, 서비스 `kgr:svc_<ID>`, 지역 `kgr:region_서울_<구>`(산하 기관은 기관명의 구 이름으로), 개념 `kgc:ut_·st_·f_·ot_·dim_`, 대상 범주 `kgc:<라벨>`, OR 묶음 `kgr:grp_<ID>_<차원>`, `budgetLimited`(예산 소진·예산 범위), `incomeMinPct`는 "초과" 값.

## 설계서와 다르게 정한 것

| 항목 | 결정 | 이유 |
|---|---|---|
| SPARQL 엔진 | pyoxigraph | rdflib은 `broader*`·`partOf*` 질의 하나에 10분을 넘겼다. pyoxigraph는 0.01~50초. 파일 기반이라는 원칙은 같다. |
| LLM | Qwen3-32B (오픈 모델, 로컬 vLLM) | 표본 40건 비교에서 Qwen3-30B-A3B-Instruct보다 AND/OR 구분이 정확했다(`archive/snapshot_2577/data/llm/target_sample40*.jsonl`). |
| LLM 출력 방식 | 1차 자유 생성 → JSON 파싱·스키마 검사, 실패분만 2차 문법 강제 생성 | 문법 강제만 쓰면 문자열 안에 공백이 무한히 생기거나, JSON 조각(`"],"`, `"age_min"`)이 범주 값으로 새어 들어왔다. |
| 합치기 전 정리 | 범주 같지 않은 표현(20자 초과, 법 조항·문장, 국민·개인 같은 일반어) 제거. requires 3개 이상은 나열로 보고 any_of로. requires/any_of와 모순되는 excludes 제거 | 모델이 대상 나열을 AND로 넣는 경우가 남아 있었다. |
| 신청기한 LLM 검증 | LLM이 낸 날짜의 월·일 숫자가 원문에 있어야 채택한다(월만 쓴 표현의 1일·말일은 허용). 날짜 없는 RANGE/FROM은 VARIES로 바꾼다 | "전월 셋째주", "매월 20일" 같은 표현에서 모델이 날짜를 지어냈다. |
| 범주 이름 맞추기 | 2회 이상 나온 표현은 순차 처리(개념 목록이 자람), 1회 표현은 완성된 목록 기준으로 병렬 처리. 합치기·상위 관계는 예/아니오로 한 번 더 검증 | 순차 처리만 하면 1시간을 넘고, 검증 없이는 절반 가까이 틀렸다. |
| 자격 판정 질의(Q0·Q1) | 자격 조건(requires 또는 OR 묶음)이 있는 사업만 센다 | 설계서 5.3의 "77건"이 이 정의다. 조건이 전혀 없는 사업(관람료 할인 등)까지 세면 413건이 된다. |

## 결과

### 그래프 규모

| 항목 | 기본 KG (`kg_base.ttl`) | 최종 KG (`kg.ttl`) |
|---|---|---|
| 트리플 | 42,656 | 61,863 |
| Service / Organization / Region | 2,461 / 200 / 27 | 같음 |
| TargetGroup 개념 | 37 (JA 코드) | 2,894 (JA 37 + 원문 추출) |
| requires | 1,738 | 2,571 |
| RequirementGroup (anyOf) | 376 (965) | 938 (3,491) |
| excludes / prefers | 0 / 0 | 548 / 222 |
| 상위 개념(broader, 대상 범주) | 0 | 1,367 |
| recipientOf | 0 | 8 |
| 성별 제한 / 소득 제한 / budgetLimited | 103 / 450 / 49 | 소득은 원문 값으로 125건 교체, 28건 추가 |

기본 KG는 설계서 5.1의 실측치(Service 2,461, Organization 200, Region 27, 개념 88, TargetGroup 37, requires 1,738, RequirementGroup 376, anyOf 965, 성별 103, 소득 450)와 같다.

### 신청기한

| 유형 | 기본 KG (규칙) | 최종 KG |
|---|---|---|
| ALWAYS | 1,258 | 1,270 |
| VARIES | 670 | 694 |
| ANNUAL | 131 | 176 |
| RANGE | 114 | 127 |
| EVENT | 69 | 80 |
| NO_APPLICATION | 43 | 54 |
| FROM / UNTIL | 14 / 1 | 22 / 1 |
| **UNKNOWN** | **161** | **37** |

UNKNOWN 161건 중 LLM 결과 144건을 반영했다(그중 LLM도 UNKNOWN 20건). 날짜 근거가 없는 17건은 버렸다.

### 지원대상 합치기 (설계서 5.2 규칙)

| 경우 | 서비스 수 |
|---|---|
| JA만 있음 → 유지 | 247 |
| 같음 → 유지 | 350 |
| LLM만 있음 → 추가 | 587 |
| **다름 → LLM으로 교체** | **1,277** |

- 바뀐 내역은 모두 `output/resolution_log.csv`에 있다(연령·소득 변경 포함 2,302행).
- 대부분 원문에 더 가깝게 구체화된다. 예: 「1인가구」 → 「독거노인 & (기초생활수급자 | 차상위계층)」
- 잘못된 경우도 있다. 예: 통합공공임대의 두 공급 유형(일반공급·우선공급)을 AND로 묶음

### 범주 이름 맞추기

- 표현 3,550개 중 범주가 아닌 353개(조항·문장 등)를 뺐다.
- 띄어쓰기 수준을 넘는 합치기 1,451쌍 중 787쌍, 상위 관계 1,797개 중 582개를 예/아니오 검증으로 되돌렸다.
- JA 결합 개념 'X/Y'의 하위 개념 12개(대학생 ⊂ 대학생/대학원생 등)와 수동 시드 2개(기초생활수급자·차상위계층 ⊂ 저소득층)를 더했다.

### Competency Question (`output/query_results.md`)

| 질문 | 기본 KG | 최종 KG |
|---|---|---|
| Q0. 중구 22세 대학생·무주택·중위소득 30% (설계서 5.3) | **77** | **41** |
| Q1. 중구 22세 기초생활수급자 대학생 | 54 | 69 |
| Q2. 무주택자 대상 현금 지원 | 37 | 35 |
| Q3. 대학생이 제외되는 사업 | 0 | 9 |
| Q4. 다른 서비스 수혜자 우대 (prefers + recipientOf) | 0 | 0 |
| Q5. 이번 달(2026-10) 신청 가능한 장학금 | 8 | 8 |

- Q0의 기본 KG 77건은 설계서 5.3의 수치와 같다. 「가정폭력피해자 국민임대주택 우선 입주권」은 최종 KG에서 「가정폭력 피해자 & 무주택세대」 조건을 갖게 되어 빠진다.
- Q1이 늘어난 것은 원문에서 「기초생활수급자」 조건이 새로 연결된 사업이 생겼기 때문이다.
- Q4는 recipientOf 8개가 생겼지만 그 범주를 우대(prefers)하는 사업이 없어 0건이다.

### LLM 추출 정확도 (표본 50건)

무작위 표본 50건(seed 20261010)의 Qwen 추출 결과를 원문과 대조했다. **판정은 Claude(AI)가 했다. 사람의 정답 표시가 아니므로 발표 전 팀이 일부를 다시 확인할 것.** 판정 근거는 `data/eval/eval50_judgments.csv`, 집계는 `data/eval/eval50_summary.json`.

- 항목 판정: 정답(원문 근거 있고 포함·제외·우대 방향이 맞음) / 근거 없음 / 범주 아님(의미 없는 말이나 거주지·조건 문장). AND/OR 구조는 서비스 단위로 따로 판정.

| 지표 | 결과 |
|---|---|
| 항목 정밀도 | **93.8%** (129개 중 121개; 서비스 평균 89.2%) |
| 근거 없음 / 범주 아님 | 3 / 5 |
| 누락된 조건 | 8건 (8개 서비스) |
| AND/OR 구조 정답 (항목 2개 이상인 서비스) | **15/24 (62.5%)** |
| 연령·소득 수치 정답 | 4/8 |
| 완전 정답 서비스 | **29/50 (58%)** |

- 주요 오류는 구조다. "A 및 B", "A 또는 B"로 나열된 대상을 AND로 넣거나, 대안 트랙 여러 개를 묶음끼리 AND로 이었다.
- 수치 오류 예: 하위 수당 하나에만 붙은 "만 65세 이상"을 서비스 전체 조건으로 넣음, 출생연도(1962~2012년)를 연령으로 잘못 환산.
- 합치기 전 정리 규칙이 표본에서 15개 항목을 버렸는데 그중 13개는 정답이었다(긴 제외 조건 문장, 단체 이름 등). 문장을 버리는 대신 짧은 범주로 바꾸는 쪽이 낫다.

## 한계와 남은 일

- **교체 규칙(미결 2)**: "다르면 LLM으로 교체"를 그대로 적용해 1,277건이 바뀌었다. OR 묶음끼리 AND로 묶는 오류가 남아 있어 `resolution_log.csv` 검수나 "JA ∪ LLM" 규칙과의 비교가 필요하다.
- **개념 긴 꼬리**: TargetGroup 2,894개 중 2,359개가 서비스 하나에서만 쓰인다. 질의에 필요한 핵심 개념부터 계층을 다듬어야 한다.
- **recipientOf 범위**: 개념 이름이 서비스명과 정확히 같을 때만 연결된다(「근로장려금 수급자」 ↔ 「근로·자녀장려금」은 연결 안 됨).
- **질의 속도**: 최종 KG에서 자격 판정 질의(Q0·Q1)는 약 45초 걸린다.
- **GPU 드라이버**: 2026-10-10 서버 드라이버가 업데이트되고 재부팅 전이라 NVML이 초기화되지 않는다. `llm_extract.py`는 이때만 vLLM의 NVML 없는 CUDA 경로를 쓰도록 우회한다. 재부팅 후에는 아무것도 바꾸지 않는다.
- 미결 1(법인·소상공인 포함 여부)은 팀 데이터대로 포함했고(`forUserType`으로 거를 수 있음), 미결 3(신청기간 여러 개)은 가장 이른 기간만 넣었다.
