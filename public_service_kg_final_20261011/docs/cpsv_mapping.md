# CPSV-AP / CCCEV 대응표

`kg:` 어휘는 CPSV-AP 3.2.0, CCCEV 2.1.0, SKOS 위에 얹은 확장이다. 대응이 있는 항목만 `rdfs:subClassOf` / `rdfs:subPropertyOf`로 스키마에 기록했다(`scripts/kg_common.py`의 `CLASSES`, `OBJECT_PROPS`).

- 이 KG 의 네임스페이스: `kg:` = `http://kg.example.org/ontology#`, `kgc:` = `.../concept/`, `kgr:` = `.../resource/` (팀 기본 KG 와 같음)
- 외부 어휘: `cpsv:` = `http://purl.org/vocab/cpsv#`, `cv:` = `http://data.europa.eu/m8g/` (CPSV-AP·CCCEV 공용), `dct:` = Dublin Core Terms
- IRI는 명세 문서를 기준으로 기억에 의존해 적었다. 외부 시스템과 연계하기 전에 공식 명세(SEMIC)와 한 번 대조할 것.

## 클래스

| kg | 대응 | 비고 |
|---|---|---|
| `kg:Service` | ⊑ `cpsv:PublicService` | |
| `kg:Organization` | ⊑ `cv:PublicOrganisation` | |
| `kg:Region` | ⊑ `dct:Location` | |
| `kg:RequirementGroup` | ⊑ `cv:Requirement` (CCCEV) | OR 묶음. CCCEV의 Requirement 계층을 단순화한 것 |
| 대상 범주 «TargetGroup» | `skos:Concept` (scheme `kgc:scheme_TargetGroup`) | CCCEV `cv:Criterion` 대신 SKOS 개념으로 두어 계층(`skos:broader`)과 별칭(`skos:altLabel`)을 쓴다 |

## 관계

| kg | 대응 | 비고 |
|---|---|---|
| `kg:providedBy` | ⊑ `cv:hasCompetentAuthority` | |
| `kg:coversRegion` | ⊑ `dct:spatial` | |
| `kg:inField` | ⊑ `cv:thematicArea` | |
| `kg:hasSupportType` | ⊑ `dct:type` | |
| `kg:hasRequirementGroup` | ⊑ `cv:holdsRequirement` | |
| `kg:partOf` | ⊑ `dct:isPartOf` | |
| `kg:forUserType` | — | CPSV-AP에 대상 사용자 유형 속성이 없어 확장 |
| `kg:requires`, `kg:excludes`, `kg:prefers`, `kg:anyOf` | — | CCCEV는 요건을 Requirement 노드로 표현하지만, 설계 원칙(엣지 의미는 type만으로)에 맞춰 개념으로 바로 잇는 확장 관계를 둠 |
| `kg:locatedIn`, `kg:hasOrgType`, `kg:recipientOf` | — | 확장 |

## 속성

| kg | 대응 |
|---|---|
| 서비스명 | `dct:title` 직접 사용 |
| 서비스ID | `dct:identifier` 직접 사용 |
| `kg:ageMin/ageMax`, `kg:incomeMinPct/incomeMaxPct`, `kg:gender` | 확장 (CCCEV에서는 Criterion 아래 InformationConcept 로 표현할 수치 조건) |
| `kg:deadline*`, `kg:apply*` | 확장 |
| `kg:targetText`, `kg:benefitText`, `kg:deadlineText` | 확장 (원문 보존) |
