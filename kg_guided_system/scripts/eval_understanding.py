"""① 프로필 추출 + ② 개념 연결 평가 (data/understanding_eval.jsonl, 문장 15개).

연구실 vLLM 이 떠 있을 때:
    LLM_BASE_URL=http://localhost:8000/v1 python scripts/eval_understanding.py
LLM 호출은 cache/llm_cache.jsonl 에 남으므로, 이후에는 GPU 없이 재현할 수 있다:
    LLM_CACHE_MODE=only python scripts/eval_understanding.py

채점
  - 수치·지역: 정답이 있는 항목만. 소득은 ±2%p.
  - 범주: 정답 항목마다 허용 라벨 목록이 있고, 연결된 개념(규칙 제외) 중 하나라도 들어 있으면 맞음 → 재현율.
  - 개념 정밀도(방향 반영): 연결된 개념 하나하나를 본다 (규칙 제외).
          해당한다 → 정답 개념이거나 그 KG 상위 개념이면 맞음 (더 좁거나 다른 개념은 틀림: 의료급여 수급자 등)
          해당하지 않는다 → 정답 개념이거나 그 KG 하위 개념이면 맞음
  - 방향 규칙으로 뺀 수: LLM 이 골랐지만 linker.enforce_direction 이 뺀 개념 수 (LLM 이 방향을 어긴 횟수)

data/understanding_eval.jsonl (15) 은 프롬프트를 고칠 때 본 문장(개발용).
data/understanding_heldout.jsonl (10) 은 1차 수정 때는 보지 않았지만 2차 수정(2026-11-19 RUN_REPORT2)에 쓰였다.
data/understanding_heldout2.jsonl (10) 은 마지막 수정까지 보지 않은 문장(최종 확인용)이다. 발표에는 셋을 따로 보고한다.
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.llm import LLMClient  # noqa: E402
from judge.pipeline import DEFAULT_KG, Consultant  # noqa: E402

TODAY = dt.date(2026, 11, 18)


def concept_precision(kg, links, gold_items, positive):
    """개념 단위, 방향 반영 정밀도. 반환: (맞은 개념 수, 전체 개념 수, 틀린 라벨 목록)."""
    gold = {c for item in gold_items for lab in item for c in kg.concepts_for_label(lab)}
    ok_set = set()
    for g in gold:
        ok_set |= kg.ancestors(g) if positive else kg.descendants(g)
    good, n, bad = 0, 0, []
    for ln in links:
        if ln.positive != positive or ln.method == "rule":
            continue
        for c in ln.concepts:
            n += 1
            if c in ok_set:
                good += 1
            else:
                bad.append(("" if positive else "¬") + kg.label(c))
    return good, n, bad


def score_groups(kg, links, gold_items, positive):
    linked = [ln for ln in links if ln.positive == positive and ln.method != "rule"]
    labels = [{kg.label(c) for c in ln.concepts} for ln in linked]
    allowed = [set(item) for item in gold_items]
    hit = sum(1 for item in allowed if any(item & lab for lab in labels))
    good = sum(1 for lab in labels if lab and any(item & lab for item in allowed))
    return hit, len(allowed), good, sum(1 for lab in labels if lab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "understanding_eval.jsonl"))
    ap.add_argument("--kg", default=str(DEFAULT_KG))
    ap.add_argument("--llm-link", action="store_true", help="개념 연결에 LLM 확인을 씀 (시스템 기본은 끔, 비교용)")
    ap.add_argument("--no-llm-link", action="store_true", help="(이전 옵션, 지금은 기본값)")
    ap.add_argument("--out", default=str(ROOT / "results" / "understanding_eval.jsonl"))
    a = ap.parse_args()

    llm = LLMClient()
    c = Consultant.from_paths(a.kg, llm, link_with_llm=a.llm_link and not a.no_llm_link)
    kg = c.kg
    rows = [json.loads(x) for x in Path(a.data).read_text(encoding="utf-8").splitlines() if x.strip()]
    tot = {"field_ok": 0, "field_n": 0, "g_hit": 0, "g_n": 0, "g_good": 0, "g_links": 0,
           "n_hit": 0, "n_n": 0, "n_good": 0, "n_links": 0, "kw_hit": 0, "kw_n": 0, "guided": 0,
           "cp_good": 0, "cp_n": 0, "rejected": 0}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            g = r["gold"]
            u = c.understand(r["text"], TODAY)
            p, ex = u.profile, u.extraction
            errs = []
            checks = {"age": p.age, "gender": p.gender, "user_type": ex.user_type,
                      "region": p.region.rsplit("region_", 1)[1] if p.region else None}
            for k, v in checks.items():
                if k in g:
                    tot["field_n"] += 1
                    if v == g[k]:
                        tot["field_ok"] += 1
                    else:
                        errs.append(f"{k}: {v} (정답 {g[k]})")
            if "income_pct" in g:
                tot["field_n"] += 1
                if p.income_pct is not None and abs(p.income_pct - g["income_pct"]) <= 2:
                    tot["field_ok"] += 1
                else:
                    errs.append(f"income_pct: {p.income_pct} (정답 {g['income_pct']})")
            h, n, good, nl = score_groups(kg, u.links, g.get("groups", []), True)
            tot["g_hit"] += h; tot["g_n"] += n; tot["g_good"] += good; tot["g_links"] += nl
            if h < n:
                errs.append(f"범주 누락: 정답 {g['groups']}")
            h, n, good, nl = score_groups(kg, u.links, g.get("not_groups", []), False)
            tot["n_hit"] += h; tot["n_n"] += n; tot["n_good"] += good; tot["n_links"] += nl
            if h < n:
                errs.append(f"부정 범주 누락: 정답 {g['not_groups']}")
            for positive in (True, False):
                good, n, bad = concept_precision(kg, u.links, g.get("groups" if positive else "not_groups", []), positive)
                tot["cp_good"] += good
                tot["cp_n"] += n
                if bad:
                    errs.append("틀린 개념(방향 반영): " + ", ".join(bad))
            tot["rejected"] += sum(len(ln.rejected) for ln in u.links)
            for alt in g.get("receiving_or_groups", []):
                got = set(ex.receiving) | {kg.label(cc) for ln in u.links for cc in ln.concepts}
                tot["g_n"] += 1
                if got & set(alt):
                    tot["g_hit"] += 1
                else:
                    errs.append(f"수급 사업 누락: {alt}")
            for kw in g.get("keywords", []):
                tot["kw_n"] += 1
                if any(kw in k or k in kw for k in ex.keywords):
                    tot["kw_hit"] += 1
                else:
                    errs.append(f"키워드 누락: {kw} (추출 {ex.keywords})")
            tot["guided"] += ex.mode == "guided"
            print(f"[{r['id']}] {'OK' if not errs else '; '.join(errs)}")
            for line in u.summary(kg):
                print("      " + line)
            f.write(json.dumps({"id": r["id"], "errors": errs, "raw": ex.raw,
                                "links": [[ln.phrase, ln.positive, [kg.label(x) for x in ln.concepts], ln.method]
                                          for ln in u.links],
                                "unresolved": u.unresolved}, ensure_ascii=False) + "\n")

    def pct(x, y):
        return f"{x}/{y} ({100 * x / y:.0f}%)" if y else "-"

    print("\n| 지표 | 결과 |\n|---|---|")
    print(f"| 수치·지역·유형 정답 | {pct(tot['field_ok'], tot['field_n'])} |")
    print(f"| 범주 재현율 | {pct(tot['g_hit'], tot['g_n'])} |")
    print(f"| 범주 연결 정밀도 (규칙 제외) | {pct(tot['g_good'], tot['g_links'])} |")
    print(f"| 개념 정밀도 (개념 단위, 방향 반영) | {pct(tot['cp_good'], tot['cp_n'])} |")
    print(f"| 방향 규칙으로 뺀 개념 (LLM 이 방향을 어긴 횟수) | {tot['rejected']} |")
    print(f"| 부정 범주 재현율 | {pct(tot['n_hit'], tot['n_n'])} |")
    print(f"| 키워드 재현율 | {pct(tot['kw_hit'], tot['kw_n'])} |")
    print(f"| 2차(문법 강제) 생성으로 넘어간 문장 | {tot['guided']}/{len(rows)} |")
    print(f"| LLM 호출 / 캐시 재사용 / 시간 | {llm.calls} / {llm.cache_hits} / {time.time() - t0:.1f}s |")


if __name__ == "__main__":
    main()
