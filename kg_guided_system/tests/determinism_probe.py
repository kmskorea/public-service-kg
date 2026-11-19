"""결정성 확인용: 같은 질문을 처리하며 LLM 에 보내는 요청 전체의 해시를 출력한다.

tests/test_fixes_round3.py 가 PYTHONHASHSEED 를 바꾼 하위 프로세스로 실행해 해시가 같은지 본다.
요청이 실행마다 다르면 캐시 키가 달라져 GPU 없이 재생할 수 없다.
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from judge import load_kg  # noqa: E402
from judge.pipeline import DEFAULT_KG, DEFAULT_PATCHES, Consultant  # noqa: E402
from judge.rag import TextRAG  # noqa: E402
from judge.systems import run_kg, run_rag  # noqa: E402
from tests.fake_llm import FakeLLM, universal_handler  # noqa: E402

EX = {"age": 22, "birth_year": None, "region": "서울 중구", "gender": None, "income_pct": None, "monthly_income": None,
      "household_size": None, "groups": ["대학생", "기초수급자", "한부모"], "not_groups": ["장애인"], "receiving": [],
      "user_type": "개인", "fields": ["주거·자립"], "support_types": ["현금"], "keywords": ["월세"]}
Q = "신당동 22살 대학생이고 기초수급자, 한부모예요. 장애는 없어요. 월세 지원 있나요?"

kg = load_kg(DEFAULT_KG, DEFAULT_PATCHES)
llm = FakeLLM(universal_handler(EX, {"기초수급자": {"기초생활수급자"}, "한부모": {"한부모가족"}},
                                rag_verdict=lambda t: ("확인 필요", "소득 정보 없음"),
                                answer=lambda u: "「청년월세 지원」은 확인이 필요해요 [S1]."))
today = dt.date(2026, 11, 18)
run_kg(Consultant(kg, llm), Q, today)
run_kg(Consultant(kg, llm, link_with_llm=True), Q, today)
run_rag(TextRAG(kg, llm), Q)
blob = json.dumps([b for _, b in llm.requests], ensure_ascii=False, sort_keys=True)
print(len(llm.requests), hashlib.sha256(blob.encode()).hexdigest())
