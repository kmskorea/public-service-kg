"""가짜 LLM 으로 KG·KG-base·RAG 파이프라인을 돌려, LLM 요청 본문이 프로세스(해시 시드)마다 같은지 본다."""
import datetime as dt, hashlib, json, sys
sys.path.insert(0, "."); sys.path.insert(0, "tests")
from fake_llm import FakeLLM, universal_handler
from judge.kg_index import load_kg
from judge.pipeline import DEFAULT_KG, Consultant
from judge.rag import TextRAG
from judge.systems import run_kg, run_rag
from pathlib import Path
ext = {"age": 22, "region": "서울 중구", "groups": ["대학생", "기초생활수급자", "1인가구"], "not_groups": ["장애인", "주택소유자"],
       "user_type": "개인", "fields": ["주거·자립"], "support_types": ["현금"], "keywords": ["월세"]}
syn = {"대학생": {"대학생", "대학생/대학원생", "학생"}, "기초생활수급자": {"기초생활수급자", "기초생활수급가구"}, "1인가구": {"1인가구"}}
llm = FakeLLM(universal_handler(ext, syn, rag_verdict=lambda t: ("확인 필요", "정보 부족")))
kgf = load_kg(DEFAULT_KG); kgb = load_kg(Path(DEFAULT_KG).with_name("kg_base.ttl"))
q = "신당동 사는 22살 대학생인데 기초수급자예요. 장애는 없어요. 월세 지원 있나요?"
groups = {}
for name, fn in [("KG", lambda: run_kg(Consultant(kgf, llm), q, dt.date(2026, 11, 18))),
                 ("KG-base", lambda: run_kg(Consultant(kgb, llm), q, dt.date(2026, 11, 18))),
                 ("RAG", lambda: run_rag(TextRAG(kgf, llm), q))]:
    llm.requests.clear(); out = fn()
    kinds = []
    for _, body in llm.requests:
        m = body["messages"]; c0, cl = m[0]["content"], m[-1]["content"]
        k = "extract" if "프로필 추출기" in c0 else "answer" if "상담원" in c0 else "verify" if "알려진 사실:" in cl else "rag"
        kinds.append((k, hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]))
    print(name, " ".join(f"{k}:{h}" for k, h in kinds), "| ranked", hashlib.sha256(str(out.ranked).encode()).hexdigest()[:8])
