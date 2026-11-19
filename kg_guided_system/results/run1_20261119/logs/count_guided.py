"""캐시만으로(LLM_CACHE_MODE=only) 실행을 재생하며 chat_json 호출 중 1차(free)/2차(guided) 를 호출 지점별로 센다. 코드 수정 없음(monkeypatch)."""
import collections, inspect, os, runpy, sys
os.environ["LLM_CACHE_MODE"] = "only"
ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[3])
sys.path.insert(0, ROOT); os.chdir(ROOT)
from judge import llm as L
cnt = collections.Counter()
orig = L.LLMClient.chat_json
def wrapped(self, *a, **k):
    caller = inspect.stack()[1]
    site = os.path.basename(caller.filename).replace(".py", "")
    obj, mode = orig(self, *a, **k)
    cnt[(site, mode)] += 1
    return obj, mode
L.LLMClient.chat_json = wrapped
script = sys.argv[1]; sys.argv = sys.argv[1:]
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    runpy.run_path(script, run_name="__main__")
for site in sorted({s for s, _ in cnt}):
    f, g = cnt[(site, "free")], cnt[(site, "guided")]
    print(f"{script} | {site} | free {f} | guided {g} | guided% {100*g/max(f+g,1):.1f}")
