"""캐시만으로(LLM_CACHE_MODE=only) compare_systems 를 재생하며 시스템별로
 - 요청이 이번 비교 실행 전부터 캐시에 있던 것(재사용)인지, 이번 실행에서 새로 호출된 것인지
 - chat_json 호출의 1차(free) / 2차(guided) 비율 (호출 지점별)
을 센다. 코드 수정 없음(monkeypatch). 사용: python results/logs/replay_stats.py <비교 전 캐시 줄 수> <out_dir>"""
import collections, contextlib, hashlib, inspect, io, json, os, runpy, sys
from pathlib import Path
os.environ["LLM_CACHE_MODE"] = "only"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
pre_n, out_dir = int(sys.argv[1]), sys.argv[2]
lines = (ROOT / "cache" / "llm_cache.jsonl").read_text(encoding="utf-8").splitlines()
pre = {json.loads(x)["key"] for x in lines[:pre_n]}
from judge import llm as L, systems as SY
cur = {"sys": None}
calls = collections.Counter(); modes = collections.Counter()
o_cached = L.LLMClient._cached
def cached(self, kind, body, call):
    key = hashlib.sha256(json.dumps([kind, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    calls[(cur["sys"], "reused" if key in pre else "new")] += 1
    return o_cached(self, kind, body, call)
L.LLMClient._cached = cached
o_cj = L.LLMClient.chat_json
def cj(self, *a, **k):
    site = os.path.basename(inspect.stack()[1].filename)[:-3]
    obj, mode = o_cj(self, *a, **k); modes[(cur["sys"], site, mode)] += 1
    return obj, mode
L.LLMClient.chat_json = cj
o_kg, o_rag = SY.run_kg, SY.run_rag
def run_kg(c, q, today, name="KG", *a, **k):
    cur["sys"] = name; return o_kg(c, q, today, name, *a, **k)
def run_rag(r, q, name="RAG", *a, **k):
    cur["sys"] = name; return o_rag(r, q, name, *a, **k)
SY.run_kg, SY.run_rag = run_kg, run_rag
sys.argv = ["compare_systems.py", "--pool-top", "5", "--out", out_dir]
with contextlib.redirect_stdout(io.StringIO()):
    runpy.run_path(str(ROOT / "scripts" / "compare_systems.py"), run_name="__main__")
for s in ("KG", "KG-base", "RAG"):
    print(f"{s}: 새 호출 {calls[(s, 'new')]} · 재사용 {calls[(s, 'reused')]}")
for (s, site, m), v in sorted(modes.items()):
    print(f"  {s} {site} {m} {v}")
