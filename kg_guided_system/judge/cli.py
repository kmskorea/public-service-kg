"""판정기 명령줄 실행.

    python -m judge.cli --age 22 --region 중구 --groups 대학생 기초생활수급자 --today 2026-11-18
    python -m judge.cli --age 22 --region 중구 --income 30 --groups 대학생 대학생/대학원생 무주택세대 --closed --targeted-only
"""
import argparse
import datetime as dt
import time
from pathlib import Path

from .judge import Judge, Profile, Status, Tier, missing_info
from .kg_index import load_kg

DEFAULT_KG = Path(__file__).resolve().parent.parent.parent / "public_service_kg_final_20261011" / "output" / "kg.ttl"
MARK = {Status.MET: "✓", Status.UNMET: "✗", Status.UNKNOWN: "?"}


def show(j, verbose: bool):
    s = j.service
    extra = []
    if j.apply:
        extra.append(j.apply.value)
    if j.preferred:
        extra.append("우대: " + ", ".join(j.preferred))
    print(f"  · {s.title}  [{s.id}]" + (f"  ({' / '.join(extra)})" if extra else ""))
    for c in j.conditions:
        if c.kind == "user_type" and c.status is Status.MET and not verbose:
            continue
        ev = f"  ← {c.evidence}" if c.evidence else ""
        print(f"      {MARK[c.status]} {c.text}{ev}")
    if j.caveats:
        print("      ! 다음에 해당하면 제외: " + ", ".join(j.caveats))
    if verbose and s.detail_url:
        print(f"      {s.detail_url}")


def main():
    ap = argparse.ArgumentParser(description="KG 기반 공공서비스 자격 판정")
    ap.add_argument("--kg", default=str(DEFAULT_KG))
    ap.add_argument("--age", type=int)
    ap.add_argument("--region")
    ap.add_argument("--income", type=float, help="기준중위소득 %%")
    ap.add_argument("--gender", choices=["여성", "남성"])
    ap.add_argument("--groups", nargs="*", default=[], help="해당하는 범주")
    ap.add_argument("--not-groups", nargs="*", default=[], help="해당하지 않는 범주")
    ap.add_argument("--receiving", nargs="*", default=[], help="이미 받는 서비스 (ID 또는 제목)")
    ap.add_argument("--user-types", nargs="*", default=["개인"])
    ap.add_argument("--today", help="YYYY-MM-DD, 주면 신청 가능 여부도 표시")
    ap.add_argument("--closed", action="store_true", help="SPARQL 과 같은 닫힌 세계 판정")
    ap.add_argument("--targeted-only", action="store_true", help="대상 조건이 있는 사업만")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    t = time.perf_counter()
    J = Judge(load_kg(a.kg))
    load_s = time.perf_counter() - t
    p = Profile(age=a.age, region=a.region, income_pct=a.income, gender=a.gender, groups=a.groups,
                not_groups=a.not_groups, receiving=a.receiving, user_types=a.user_types)
    res = J.run(p, mode="closed" if a.closed else "open", targeted_only=a.targeted_only,
                today=dt.date.fromisoformat(a.today) if a.today else None)

    print(f"KG 로드 {load_s:.2f}s · 판정 {res.seconds * 1000:.0f}ms · 서비스 {len(J.kg.services)}건")
    if res.prepared.unresolved:
        print("KG에서 찾지 못한 표현:", ", ".join(res.prepared.unresolved))
    for tier in (Tier.ELIGIBLE, Tier.NEEDS_INFO):
        js = res.by_tier(tier)
        print(f"\n■ {tier.value}: {len(js)}건" + (f" (상위 {a.top}건)" if len(js) > a.top else ""))
        for j in js[: a.top]:
            show(j, a.verbose)
    mi = missing_info(res)
    if mi:
        print("\n■ 더 알면 판정이 정리되는 정보 (확인 필요 서비스 수)")
        for name, n in mi:
            print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
