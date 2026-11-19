"""세 시스템(KG · KG-base · 텍스트 RAG)을 같은 질문으로 실행하고, 정답 표시용 풀(pool) CSV 를 만든다.

연구실 vLLM 이 떠 있을 때:
    python scripts/compare_systems.py                       # data/understanding_eval.jsonl 의 문장 15개
    LLM_CACHE_MODE=only python scripts/compare_systems.py   # 이후 GPU 없이 재현
출력 (results/)
    compare.jsonl              시스템 × 질문별 순위·답변·근거 검사·호출 수·시간
    compare.md                 사람이 읽는 비교표 (질문마다 시스템별 상위 5건과 답변)
    pool_for_annotation.csv    시스템들이 낸 상위 N 건의 합집합 + 원문 → gold 열을 채운다 (적합/확인 필요/부적합)
정답을 채운 뒤: python scripts/score_systems.py
"""
import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.kg_index import load_kg  # noqa: E402
from judge.llm import LLMClient  # noqa: E402
from judge.pipeline import DEFAULT_KG, DEFAULT_PATCHES, Consultant  # noqa: E402
from judge.rag import TextRAG  # noqa: E402
from judge.systems import run_kg, run_rag  # noqa: E402

TODAY = dt.date(2026, 11, 18)


def clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "data" / "understanding_eval.jsonl"),
                    help="jsonl, 줄마다 {id, text}")
    ap.add_argument("--systems", nargs="*", default=["KG", "KG-base", "RAG"],
                    help="KG, KG-base, RAG, KG-LLM연결 (개념 연결에 LLM 확인을 켠 KG, 비교용)")
    ap.add_argument("--pool-top", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--reuse-gold", default=None,
                    help="이전 pool CSV. 같은 (질문, 서비스) 의 gold·note 를 새 풀에 옮겨 적는다 (정답 표시를 다시 하지 않도록)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    llm = LLMClient()
    # KG 시스템은 보정(data/kg_patches.ttl)을 포함한 최종 KG, KG-base 는 수업 기본 KG 그대로
    kg_final = load_kg(DEFAULT_KG, DEFAULT_PATCHES)
    runners = {}
    if "KG" in a.systems:
        c = Consultant(kg_final, llm)
        runners["KG"] = lambda q, c=c: run_kg(c, q, TODAY, "KG")
    if "KG-LLM연결" in a.systems:
        cl = Consultant(kg_final, llm, link_with_llm=True)
        runners["KG-LLM연결"] = lambda q, cl=cl: run_kg(cl, q, TODAY, "KG-LLM연결")
    if "KG-base" in a.systems:
        cb = Consultant(load_kg(Path(DEFAULT_KG).with_name("kg_base.ttl")), llm)
        runners["KG-base"] = lambda q, cb=cb: run_kg(cb, q, TODAY, "KG-base")
    if "RAG" in a.systems:
        r = TextRAG(kg_final, llm)
        runners["RAG"] = lambda q, r=r: run_rag(r, q, "RAG")

    qs = [json.loads(x) for x in Path(a.questions).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows, pool = [], {}
    md = ["# 시스템 비교", ""]
    with open(out / "compare.jsonl", "w", encoding="utf-8") as f:
        for q in qs:
            md += [f"## {q['id']}. {q['text']}", ""]
            for name, run in runners.items():
                o = run(q["text"])
                rec = {"qid": q["id"], "system": name, "ranked": o.ranked, "seconds": round(o.seconds, 2),
                       "llm_calls": o.llm_calls,
                       "answer": o.answer.text if o.answer else None,
                       "cited": o.answer.cited if o.answer else [],
                       "warnings": o.answer.warnings if o.answer else [],
                       "first_warnings": o.answer.first_warnings if o.answer else [],
                       "answer_mode": o.answer.mode if o.answer else None, "detail": o.detail}
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                rows.append(rec)
                for rank, (sid, tier) in enumerate(o.ranked[: a.pool_top], 1):
                    pool.setdefault((q["id"], sid), {"question": q["text"], "by": {}})["by"][name] = f"{tier}#{rank}"
                el, ni = len(o.ids("적합")), len(o.ids("확인 필요"))
                md.append(f"**{name}** — 적합 {el} · 확인 필요 {ni} · LLM {o.llm_calls}회 · {o.seconds:.1f}s"
                          + (f" · 근거 검사 경고 {len(o.answer.warnings)}" if o.answer else ""))
                for sid, tier in o.ranked[:5]:
                    md.append(f"- [{tier}] {kg_final.services['http://kg.example.org/resource/svc_' + sid].title}")
                if o.answer:
                    md += ["", "> " + o.answer.text.replace("\n", "\n> ")]
                    if o.answer.warnings:
                        md.append("> ⚠ " + " / ".join(o.answer.warnings))
                md.append("")
            print(q["id"], "done", flush=True)

    (out / "compare.md").write_text("\n".join(md), encoding="utf-8")
    names = list(runners)
    old = {}
    if a.reuse_gold and Path(a.reuse_gold).exists():
        with open(a.reuse_gold, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("gold", "").strip():
                    old[(r["qid"], r["service_id"])] = (r["gold"].strip(), r.get("note", ""))
    with open(out / "pool_for_annotation.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "question", "service_id", "title", "region", "target_text", "benefit_text"]
                   + [f"sys_{n}" for n in names] + ["gold", "note"])
        for (qid, sid), v in sorted(pool.items()):
            s = kg_final.services["http://kg.example.org/resource/svc_" + sid]
            w.writerow([qid, v["question"], sid, s.title, kg_final.label(s.region), clip(s.target_text, 600),
                        clip(s.benefit_text, 200)] + [v["by"].get(n, "") for n in names]
                       + list(old.get((qid, sid), ("", ""))))
    reused = sum(1 for k in pool if k in old)
    print(f"저장: {out}/compare.jsonl, compare.md, pool_for_annotation.csv ({len(pool)}행, 이전 정답 {reused}행 옮김, "
          f"새로 표시할 행 {len(pool) - reused})")
    for n in names:
        rs = [r for r in rows if r["system"] == n]
        warn = sum(len(r["warnings"]) for r in rs)
        print(f"{n:8s} 질문 {len(rs)} · 평균 LLM {sum(r['llm_calls'] for r in rs) / len(rs):.1f}회 · "
              f"평균 {sum(r['seconds'] for r in rs) / len(rs):.1f}s · 근거 검사 경고 합계 {warn}")


if __name__ == "__main__":
    main()
