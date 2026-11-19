"""정답을 채운 풀 CSV 로 시스템을 채점한다.

    python scripts/score_systems.py [--pool results/pool_for_annotation.csv] [--compare results/compare.jsonl]

gold: 적합 / 확인 필요 / 부적합 (빈칸은 채점에서 뺀다)
지표 (시스템별, 질문 평균이 아닌 전체 합산)
  P@5, P@10       상위 k 중 gold 가 부적합이 아닌 비율 (관련 있고 틀리지 않은 추천)
  적합 정밀도       시스템이 '적합'이라 한 것 중 gold 도 적합인 비율
  위험한 오판       시스템 '적합' 인데 gold '부적합' 인 비율 (낮을수록 좋음)
  적합 재현율       풀 안의 gold 적합 중 시스템이 (어느 단계로든) 낸 비율
  답변 경고         근거 검사(없는 사업명·숫자·인용·조건부 단정)에 걸린 답변 비율. 다시 쓰기 전 → 최종
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(ROOT / "results" / "pool_for_annotation.csv"))
    ap.add_argument("--compare", default=str(ROOT / "results" / "compare.jsonl"))
    a = ap.parse_args()

    gold = {}
    with open(a.pool, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            g = r["gold"].strip()
            if g in ("적합", "확인 필요", "부적합"):
                gold[(r["qid"], r["service_id"])] = g
    if not gold:
        raise SystemExit("gold 열이 비어 있다. pool CSV 에 적합/확인 필요/부적합을 채운 뒤 다시 실행.")
    runs = [json.loads(x) for x in Path(a.compare).read_text(encoding="utf-8").splitlines() if x.strip()]
    gold_el = defaultdict(set)
    for (qid, sid), g in gold.items():
        if g == "적합":
            gold_el[qid].add(sid)

    by_sys = defaultdict(list)
    for r in runs:
        by_sys[r["system"]].append(r)
    print("| 시스템 | P@5 | P@10 | 적합 정밀도 | 위험한 오판 | 적합 재현율 | 답변 경고 | 평균 LLM 호출 | 평균 시간 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, rs in by_sys.items():
        pk = {5: [0, 0], 10: [0, 0]}
        el_ok = el_n = el_bad = 0
        rec_hit = rec_n = 0
        for r in rs:
            ranked = [tuple(x) for x in r["ranked"]]
            for k in pk:
                for sid, _ in ranked[:k]:
                    g = gold.get((r["qid"], sid))
                    if g:
                        pk[k][1] += 1
                        pk[k][0] += g != "부적합"
            for sid, tier in ranked:
                g = gold.get((r["qid"], sid))
                if tier == "적합" and g:
                    el_n += 1
                    el_ok += g == "적합"
                    el_bad += g == "부적합"
            mine = {sid for sid, _ in ranked}
            rec_n += len(gold_el[r["qid"]])
            rec_hit += len(gold_el[r["qid"]] & mine)
        warn = sum(1 for r in rs if r["warnings"])
        warn0 = sum(1 for r in rs if r.get("first_warnings", r["warnings"]))

        def pct(x, y):
            return f"{100 * x / y:.0f}% ({x}/{y})" if y else "-"

        print(f"| {name} | {pct(*pk[5])} | {pct(*pk[10])} | {pct(el_ok, el_n)} | {pct(el_bad, el_n)} | "
              f"{pct(rec_hit, rec_n)} | {pct(warn0, len(rs))} → {pct(warn, len(rs))} | {sum(r['llm_calls'] for r in rs) / len(rs):.1f} | "
              f"{sum(r['seconds'] for r in rs) / len(rs):.1f}s |")
    print(f"\n정답 표시 {len(gold)}건 (적합 {sum(len(v) for v in gold_el.values())}건)")


if __name__ == "__main__":
    main()
