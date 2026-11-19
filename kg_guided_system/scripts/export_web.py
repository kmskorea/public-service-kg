"""웹 서비스용 KG 내보내기: kg.ttl (+ data/kg_patches.ttl) → web/data/kg.json

브라우저의 판정기(web/judge.js)가 Python 판정기(judge/judge.py)와 같은 결과를 내도록 필요한 것만 담는다.
개념은 번호로 참조해 크기를 줄인다. 원문 텍스트는 화면에 보여줄 만큼 자른다.

    python scripts/export_web.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.extract import concept_usage, is_target_group  # noqa: E402
from judge.kg_index import KGC, KGR, load_kg  # noqa: E402
from judge.pipeline import DEFAULT_KG, DEFAULT_PATCHES, GENERIC_LABELS, ORG_ROOT_LABELS  # noqa: E402

OUT = ROOT / "web" / "data" / "kg.json"


def clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def export(kg_path=DEFAULT_KG, patches=DEFAULT_PATCHES, out=OUT) -> dict:
    kg = load_kg(kg_path, patches)
    usage = concept_usage(kg)

    # 개념: 라벨이 있는 KGC 개념 전부 (대상 범주 + 지원유형·분야·사용자유형 개념)
    concepts = sorted(c for c in kg.labels if c.startswith(KGC))
    idx = {c: i for i, c in enumerate(concepts)}
    alts: dict[str, list[str]] = {c: [] for c in concepts}
    for lab, cs in kg.label_to_concepts.items():
        for c in cs:
            if c in alts and lab != kg.labels[c] and lab not in alts[c]:
                alts[c].append(lab)
    org = {d for lab in ORG_ROOT_LABELS for c in kg.concepts_for_label(lab) for d in kg.descendants(c)}
    concept_rows = []
    for c in concepts:
        row = {"k": c[len(KGC):], "l": kg.labels[c],
               "p": sorted(idx[p] for p in kg.parents.get(c, ()) if p in idx)}
        if alts[c]:
            row["a"] = sorted(alts[c])
        if usage.get(c):
            row["u"] = usage[c]
        if is_target_group(c):
            row["t"] = 1                       # 대상 범주 (검색·칩 대상)
        if c in org:
            row["o"] = 1                       # 법인·기관 계열 (개인에게 되묻지 않음)
        concept_rows.append(row)

    regions = sorted(kg.region_labels)
    ridx = {r: i for i, r in enumerate(regions)}
    region_rows = [{"k": r[len(KGR):], "l": kg.region_labels[r],
                    "p": sorted(ridx[p] for p in kg.region_parent.get(r, ()) if p in ridx)} for r in regions]

    services = []
    for s in sorted(kg.services.values(), key=lambda x: x.id):
        row = {
            "id": s.id, "t": s.title, "r": ridx[s.region], "tier": s.tier,
            "ut": sorted(idx[u] for u in s.user_types if u in idx),
            "st": sorted(idx[x] for x in s.support_types if x in idx),
            "f": kg.label(s.field_) if s.field_ else "", "o": kg.label(s.org) if s.org else "",
            "req": sorted(idx[c] for c in s.requires),
            "grp": [sorted(idx[c] for c in s.groups[g]) for g in sorted(s.groups)],
            "exc": sorted(idx[c] for c in s.excludes), "pref": sorted(idx[c] for c in s.prefers),
            "dk": s.deadline_kind, "dt": clip(s.deadline_text, 160),
            # 원문은 자르지 않는다: 질문 관련성(키워드가 지원내용에 있는가)이 Python 과 같아야 한다
            "tt": (s.target_text or "").strip(), "bt": s.benefit_text or "", "url": s.detail_url,
        }
        for k, v in (("amin", s.age_min), ("amax", s.age_max), ("imax", s.income_max_pct),
                     ("imin", s.income_min_pct), ("g", s.gender),
                     ("as", s.apply_start.isoformat() if s.apply_start else None),
                     ("ae", s.apply_end.isoformat() if s.apply_end else None),
                     ("asm", s.apply_start_month), ("aem", s.apply_end_month)):
            if v is not None:
                row[k] = v
        if s.budget_limited:
            row["bl"] = 1
        services.append(row)

    data = {
        "meta": {
            "source": "공공서비스 KG (public_service_kg_final_20261011/output/kg.ttl) + kg_patches.ttl",
            "snapshot": "2026-10",
            "exported": dt.date.today().isoformat(),
            "services": len(services), "concepts": len(concept_rows), "regions": len(region_rows),
            "generic_labels": list(GENERIC_LABELS),
        },
        "concepts": concept_rows, "regions": region_rows, "services": services,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data


if __name__ == "__main__":
    d = export()
    print(f"{OUT} · 서비스 {d['meta']['services']} · 개념 {d['meta']['concepts']} · "
          f"{OUT.stat().st_size / 1e6:.2f} MB")
