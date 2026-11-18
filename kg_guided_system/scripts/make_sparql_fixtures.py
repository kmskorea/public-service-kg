"""기존 SPARQL 질의(Q0·Q1·Q5)를 pyoxigraph로 실행해 판정기 검증용 정답(서비스 ID 목록)을 만든다.

질의 하나에 수십 초가 걸리므로 결과를 tests/fixtures/ 에 저장해 두고 테스트는 그 파일과 비교한다.

    python scripts/make_sparql_fixtures.py [--kg-dir ../public_service_kg_final_20261011]
"""
import argparse
import json
import re
import time
from pathlib import Path

import pyoxigraph as ox

HERE = Path(__file__).resolve().parent.parent
QUERIES = ["q0_eligible_junggu_homeless", "q1_eligible_junggu_basic_student", "q5_scholarship_this_month"]


def with_service_var(q: str) -> str:
    """결과에 서비스 IRI(?s)를 넣는다. 제목이 같은 서비스가 있어도 ID 단위로 비교하기 위해서다."""
    q = re.sub(r"SELECT (DISTINCT )?\?title", r"SELECT \1?s ?title", q, count=1)
    q = q.replace("GROUP BY ?title", "GROUP BY ?s ?title")
    return q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kg-dir", default=str(HERE.parent / "public_service_kg_final_20261011"))
    args = ap.parse_args()
    kg_dir = Path(args.kg_dir)
    prefix = (kg_dir / "queries" / "_prefix.rq").read_text(encoding="utf-8")
    out = {}
    for kg in ["kg_base.ttl", "kg.ttl"]:
        store = ox.Store()
        store.load(path=str(kg_dir / "output" / kg), format=ox.RdfFormat.TURTLE)
        for name in QUERIES:
            q = with_service_var((kg_dir / "queries" / f"{name}.rq").read_text(encoding="utf-8"))
            t = time.time()
            rows = list(store.query(prefix + q))
            ids = sorted({r["s"].value.rsplit("svc_", 1)[1] for r in rows})
            out[f"{kg}:{name}"] = {"ids": ids, "rows": len(rows), "seconds": round(time.time() - t, 1)}
            print(kg, name, "rows", len(rows), "ids", len(ids), f"{time.time() - t:.1f}s", flush=True)
    dst = HERE / "tests" / "fixtures" / "sparql_results.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", dst)


if __name__ == "__main__":
    main()
