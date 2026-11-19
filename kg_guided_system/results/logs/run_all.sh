#!/bin/bash
# round3 최종 실행: 이해도 6회 + 시스템 비교 + 시간 측정(캐시 끔, 3문항)
source ~/anaconda3/etc/profile.d/conda.sh; conda activate pskg
cd "$(dirname "$0")/../.."
export LLM_CACHE_MODE=use
t(){ local n=$1; shift; local s=$(date +%s); echo "### START $n $(date +%T)"; "$@"; local rc=$?; echo "### END $n rc=$rc elapsed=$(( $(date +%s)-s ))s $(date +%T)"; }
for d in "dev:understanding_eval" "heldout:understanding_heldout" "heldout2:understanding_heldout2"; do
  n=${d%%:*}; f=${d##*:}
  t ${n}_base python scripts/eval_understanding.py --data data/$f.jsonl --out results/understanding_${n}.jsonl > results/logs/eval_${n}.log 2>&1
  t ${n}_llmlink python scripts/eval_understanding.py --data data/$f.jsonl --llm-link --out results/understanding_${n}_llmlink.jsonl > results/logs/eval_${n}_llmlink.log 2>&1
done
t compare python scripts/compare_systems.py --pool-top 5 > results/logs/compare_systems.log 2>&1
LLM_CACHE_MODE=off t timing python scripts/compare_systems.py --questions results/logs/timing_questions.jsonl --out results/timing > results/logs/timing.log 2>&1
echo ALLDONE
