#!/bin/bash
source ~/anaconda3/etc/profile.d/conda.sh; conda activate pskg
cd /dataset/Data1/minseo/pskg_judge/kg_guided_system
export LLM_CACHE_MODE=use
t(){ local s=$(date +%s); echo "### START $1 $(date +%T)"; shift; "$@"; local rc=$?; echo "### END rc=$rc elapsed=$(( $(date +%s)-s ))s $(date +%T)"; }
t ask python -m judge.ask "신당동 사는 22살 대학생인데 기초수급자예요. 장애는 없어요. 월세 지원 있나요?" --today 2026-11-18 > results/ask_example.txt 2>&1; tail -1 results/ask_example.txt
t eval_llm python scripts/eval_understanding.py > results/logs/eval_understanding.log 2>&1
t eval_nollm python scripts/eval_understanding.py --no-llm-link --out results/understanding_eval_nollm.jsonl > results/logs/eval_understanding_nollm.log 2>&1
t compare python scripts/compare_systems.py --pool-top 5 > results/logs/compare_systems.log 2>&1
echo ALLDONE
