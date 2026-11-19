#!/bin/bash
source ~/anaconda3/etc/profile.d/conda.sh; conda activate pskg
cd "$(dirname "$0")/../.."
export LLM_CACHE_MODE=use
t(){ local n=$1; shift; local s=$(date +%s); echo "### START $n $(date +%T)"; "$@"; local rc=$?; echo "### END $n rc=$rc elapsed=$(( $(date +%s)-s ))s $(date +%T)"; }
t eval_llm python scripts/eval_understanding.py > results/logs/eval_understanding.log 2>&1
t eval_nollm python scripts/eval_understanding.py --no-llm-link --out results/understanding_eval_nollm.jsonl > results/logs/eval_understanding_nollm.log 2>&1
t heldout_llm python scripts/eval_understanding.py --data data/understanding_heldout.jsonl --out results/heldout_eval.jsonl > results/logs/heldout_eval.log 2>&1
t heldout_nollm python scripts/eval_understanding.py --data data/understanding_heldout.jsonl --no-llm-link --out results/heldout_eval_nollm.jsonl > results/logs/heldout_eval_nollm.log 2>&1
t compare python scripts/compare_systems.py --pool-top 5 > results/logs/compare_systems.log 2>&1
echo ALLDONE
