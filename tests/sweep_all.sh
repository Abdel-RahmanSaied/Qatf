#!/usr/bin/env bash
# The stage-2 sweep. One variable per run, scored against the baseline.
#
# Runs inside the qatf container because that is where the GPU and
# faster-whisper live. Each run writes a distinctly named transcript so runs
# compare side by side; none of them touches the transcript cache key.
#
# Ordered by the defect they target, per docs/quality.md's hypothesis table.
set -u
export MSYS_NO_PATHCONV=1
C=qutf-qatf-1

run () {
  local name="$1" overrides="$2"
  echo "=== $name  $overrides"
  docker exec "$C" python /app/tests/sweep_asr.py "$name" "$overrides" 2>&1 | tail -3
}

# 1 — dropped speech. Our own non-default 500ms is the prime suspect: the
#     faster-whisper default under vad_filter is 160, and chunks cap at 30s.
run vad160        '{"vad_parameters": {"min_silence_duration_ms": 160}}'
run vad160-pad200 '{"vad_parameters": {"min_silence_duration_ms": 160, "speech_pad_ms": 200}}'
run maxspeech15   '{"vad_parameters": {"min_silence_duration_ms": 160, "max_speech_duration_s": 15}}'

# 2 — loops and hallucinations
run norepeat3     '{"no_repeat_ngram_size": 3}'
run reppen105     '{"repetition_penalty": 1.05}'
run halluc2       '{"hallucination_silence_threshold": 2.0}'

# 3 — let a bad window actually trigger temperature fallback
run logprob08     '{"log_prob_threshold": -0.8}'
run compress22    '{"compression_ratio_threshold": 2.2}'
