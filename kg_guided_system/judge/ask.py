"""문장으로 묻기: 추출 → 연결 → 판정 결과 출력.

    python -m judge.ask "신당동 사는 22살 대학생인데 기초수급자예요. 월세 지원 있나요?"
    LLM_CACHE_MODE=only python -m judge.ask "..."      # GPU 없이 캐시로 재생 (시연용)
"""
import argparse
import datetime as dt
import sys

from .cli import show
from .judge import Tier, missing_info
from .llm import LLMClient, LLMError
from .pipeline import DEFAULT_KG, Consultant


def main():
    ap = argparse.ArgumentParser(description="KG 기반 공공서비스 상담 (추출·연결·판정)")
    ap.add_argument("text")
    ap.add_argument("--kg", default=str(DEFAULT_KG))
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--no-llm-link", action="store_true", help="개념 연결에 LLM 을 쓰지 않음")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()

    llm = LLMClient()
    c = Consultant.from_paths(a.kg, llm, link_with_llm=not a.no_llm_link)
    try:
        out = c.consult(a.text, today)
    except LLMError as e:
        sys.exit(f"{e}\n연구실 서버에서 vLLM 을 띄우고 LLM_BASE_URL 을 맞추세요 "
                 f"(예: vllm serve Qwen/Qwen3-32B --port 8000).")
    u = out.understanding
    print("\n".join(u.summary(c.kg)))
    if u.unresolved:
        print("KG에서 찾지 못한 표현:", ", ".join(u.unresolved))
    rel = [j for j in out.ranked if c.relevant(j, u.extraction)]
    other = [j for j in out.ranked if not c.relevant(j, u.extraction)]
    for name, js in (("질문과 관련된 사업", rel), ("그 밖에 받을 수 있는 사업", other)):
        if not js:
            continue
        print(f"\n■ {name}: 적합 {sum(j.tier is Tier.ELIGIBLE for j in js)}건 · "
              f"확인 필요 {sum(j.tier is Tier.NEEDS_INFO for j in js)}건")
        shown = [j for j in js if j.tier is Tier.ELIGIBLE][: a.top] if name.startswith("그 밖") else js[: a.top]
        for j in shown:
            print(f"  [{j.tier.value}]", end="")
            show(j, a.verbose)
    mi = missing_info(out.result, 5, rel or None)     # 질문과 관련된 사업이 있으면 그 안에서 묻는다
    if mi:
        print("\n■ 더 알려주시면 판정이 정리됩니다:", ", ".join(f"{n} ({k}건)" for n, k in mi))
    print(f"\n(LLM 호출 {llm.calls}회, 캐시 재사용 {llm.cache_hits}회, 판정 {out.result.seconds * 1000:.0f}ms)")


if __name__ == "__main__":
    main()
