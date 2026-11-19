#!/bin/bash
# usage: [QUANT=fp8] serve_vllm.sh <gpu> <gpu_mem_util> <max_num_seqs> [max_num_batched_tokens]
# 이번 실행: QUANT=fp8 serve_vllm.sh 0 0.55 8
source ~/anaconda3/etc/profile.d/conda.sh
conda activate llm
export VLLM_USE_FLASHINFER_SAMPLER=0
export CUDA_VISIBLE_DEVICES=$1
MODEL=$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-32B/snapshots/9216db5781bf21249d130ec9da846c4624c16137
exec vllm serve $MODEL --served-model-name Qwen/Qwen3-32B --port 8000 \
  --max-model-len 8192 --gpu-memory-utilization ${2:-0.90} --max-num-seqs ${3:-256} ${4:+--max-num-batched-tokens $4} ${QUANT:+--quantization $QUANT} 2>&1 | tee /tmp/claude-1008/-dataset-Data1-minseo/21f82aa0-9cc3-4a53-ad39-475c5d29ae1a/scratchpad/vllm_run3.log
