"""run1(수정 전) 연결 결과에 새 지표 '개념 정밀도(방향 반영)'를 사후 적용한다.
run1 jsonl 에는 개념 IRI 대신 라벨이 남아 있으므로 라벨 → concepts_for_label 로 되돌리고,
라벨 하나를 개념 하나로 센다 (라벨의 개념 중 하나라도 허용 집합에 있으면 맞음). 같은 KG(패치 포함)로 채점."""
import json, sys
ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
from judge.kg_index import load_kg
from judge.pipeline import DEFAULT_KG, DEFAULT_PATCHES
kg = load_kg(DEFAULT_KG, DEFAULT_PATCHES)
data = {json.loads(l)["id"]: json.loads(l) for l in open(f"{ROOT}/data/understanding_eval.jsonl", encoding="utf-8")}
good = n = 0; bads = {}
for l in open(sys.argv[1], encoding="utf-8"):
    r = json.loads(l); g = data[r["id"]]["gold"]
    for positive in (True, False):
        items = g.get("groups" if positive else "not_groups", [])
        gold = {c for item in items for lab in item for c in kg.concepts_for_label(lab)}
        ok = set()
        for c in gold:
            ok |= kg.ancestors(c) if positive else kg.descendants(c)
        for phrase, pos, labels, method in r["links"]:
            if pos != positive or method == "rule":
                continue
            for lab in labels:
                n += 1
                if kg.concepts_for_label(lab) & ok:
                    good += 1
                else:
                    bads.setdefault(r["id"], []).append(("" if positive else "¬") + lab)
print(f"개념 정밀도(방향 반영, 사후 채점): {good}/{n} ({100*good/n:.0f}%)")
for k, v in bads.items(): print(" ", k, v)
