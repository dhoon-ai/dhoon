#!/usr/bin/env bash
# Optional, any time: measure Ollama's speed with its default setup (which uses a
# supported graphics card if it finds one) versus CPU only, and keep the faster one.
#
#   bash scripts/benchmark.sh            measure, then apply the winner
#   bash scripts/benchmark.sh --report   measure only, change nothing
#   bash scripts/benchmark.sh --reset    remove the CPU-only setting again
#   bash scripts/benchmark.sh --vulkan   also test integrated/other GPUs via llama.cpp
#                                        Vulkan (report only; takes ~20 min to build)
#
# Why: on many mini PCs and laptops the graphics chip shares system memory and is
# slower than the CPU; splitting a model between them can be slowest of all.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
BENCH_MODEL="${BENCH_MODEL:-$MODEL_FAST}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system/ollama.service.d}"
DROPIN="$SYSTEMD_DIR/cpu-only.conf"
RESULTS="$RUNTIME/benchmark.txt"
MODE="apply"
VULKAN=false
for arg in "$@"; do
  case "$arg" in
    --report) MODE="report" ;;
    --reset)  MODE="reset" ;;
    --vulkan) VULKAN=true ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

restart_ollama() {
  [ "${NO_RESTART:-}" = "1" ] && return 0
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  for _ in $(seq 1 30); do curl -fs "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && return 0; sleep 1; done
  fail "Ollama did not come back after restart"; return 1
}

write_dropin() {
  local tmp
  tmp=$(mktemp)
  cat > "$tmp" <<'EOF'
# Written by scripts/benchmark.sh: CPU-only was measured to be faster on this PC.
# Remove with:  bash scripts/benchmark.sh --reset
[Service]
Environment="CUDA_VISIBLE_DEVICES=-1"
Environment="HIP_VISIBLE_DEVICES=-1"
Environment="ROCR_VISIBLE_DEVICES=-1"
Environment="GGML_VK_VISIBLE_DEVICES="
Environment="OLLAMA_VULKAN=0"
EOF
  if [ -w "$SYSTEMD_DIR" ] 2>/dev/null; then mv "$tmp" "$DROPIN"; else sudo mkdir -p "$SYSTEMD_DIR"; sudo mv "$tmp" "$DROPIN"; fi
}

remove_dropin() {
  if [ -f "$DROPIN" ]; then
    if [ -w "$SYSTEMD_DIR" ]; then rm -f "$DROPIN"; else sudo rm -f "$DROPIN"; fi
    return 0
  fi
  return 1
}

if [ "$MODE" = "reset" ]; then
  step "Removing the CPU-only setting"
  if remove_dropin; then restart_ollama; ok "Ollama is back to its default (uses a supported GPU if present)"
  else ok "No CPU-only setting was active"; fi
  exit 0
fi

# Run one generation and print "prompt_tokens_per_s gen_tokens_per_s".
# $1 = extra JSON options, e.g. ',"num_gpu":0' for CPU only.
measure() {
  local extra="$1" body
  # ~600-token prompt so prompt reading is measured, not just loading.
  body=$(python3 - "$BENCH_MODEL" "$extra" <<'PY'
import json, sys
model, extra = sys.argv[1], sys.argv[2]
text = ("The quick brown fox jumps over the lazy dog while the river flows past old stone "
        "bridges and quiet villages. ") * 30
opts = json.loads('{"num_predict":128,"temperature":0,"seed":1' + extra + '}')
print(json.dumps({"model": model, "prompt": "Summarise this text in one paragraph:\n" + text,
                  "stream": False, "keep_alive": "5m", "options": opts}))
PY
)
  curl -fsS --max-time 900 "$OLLAMA_URL/api/generate" -d "$body" | python3 -c '
import json, sys
r = json.load(sys.stdin)
pp = r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9)
tg = r["eval_count"] / (r["eval_duration"] / 1e9)
print(f"{pp:.1f} {tg:.1f}")'
}

# Best of two runs after a warm-up (the first call also loads the model).
bench() {
  local extra="$1" a b
  measure "$extra" >/dev/null
  a=$(measure "$extra"); b=$(measure "$extra")
  python3 -c "
a=list(map(float,'$a'.split())); b=list(map(float,'$b'.split()))
print(f'{max(a[0],b[0]):.1f} {max(a[1],b[1]):.1f}')"
}

# Where Ollama put the model: "100% CPU", "100% GPU", "40%/60% CPU/GPU"
processor() {
  ollama ps 2>/dev/null | awk -v m="$BENCH_MODEL" '$1==m {for(i=1;i<=NF;i++) if($i ~ /CPU|GPU/){print $(i-1), $i; exit}}'
}

step "Benchmark: Ollama default vs CPU only (model: $BENCH_MODEL)"
curl -fs "$OLLAMA_URL/api/tags" >/dev/null || { fail "Ollama is not running: sudo systemctl start ollama"; exit 1; }
ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$BENCH_MODEL" || { echo "Downloading $BENCH_MODEL..."; ollama pull "$BENCH_MODEL"; }

# Measure the default without our override, so "default" really means Ollama's choice.
had_dropin=false
if [ -f "$DROPIN" ]; then
  had_dropin=true
  echo "Temporarily removing the existing CPU-only setting to measure Ollama's default..."
  remove_dropin; restart_ollama
fi

echo "Measuring Ollama's default setup (this takes 1-3 minutes)..."
read -r def_pp def_tg < <(bench "")
def_where=$(processor || true)
echo "  default:  reading ${def_pp} tok/s, writing ${def_tg} tok/s   [${def_where:-unknown}]"

echo "Measuring CPU only..."
read -r cpu_pp cpu_tg < <(bench ',"num_gpu":0')
echo "  CPU only: reading ${cpu_pp} tok/s, writing ${cpu_tg} tok/s"

# Score = seconds for a typical agent step: read 2000 tokens, write 300.
read -r def_s cpu_s winner < <(python3 -c "
d = 2000/$def_pp + 300/$def_tg
c = 2000/$cpu_pp + 300/$cpu_tg
w = 'cpu' if c < d*0.9 else 'default'      # CPU must be clearly (10%+) faster to switch
print(f'{d:.0f} {c:.0f} {w}')")
echo
echo "  Typical agent step (read 2000 tokens, write 300): default ~${def_s}s, CPU only ~${cpu_s}s"

{
  echo "date: $(date -Is)"; echo "model: $BENCH_MODEL"
  echo "default: pp=${def_pp} tg=${def_tg} where=${def_where:-unknown} step=${def_s}s"
  echo "cpu_only: pp=${cpu_pp} tg=${cpu_tg} step=${cpu_s}s"; echo "winner: $winner"
} > "$RESULTS"

case "$def_where" in
  *GPU*) gpu_used=true ;;
  *)     gpu_used=false ;;
esac

if [ "$MODE" = "report" ]; then
  if $had_dropin; then write_dropin; restart_ollama; fi
  ok "Report only - nothing changed"
elif [ "$winner" = "cpu" ] && $gpu_used; then
  write_dropin; restart_ollama
  ok "CPU only is faster on this PC. Ollama is now set to CPU only (undo: bash scripts/benchmark.sh --reset)"
elif [ "$winner" = "cpu" ]; then
  ok "Ollama already runs on the CPU here (no usable GPU found), so nothing needs changing"
else
  ok "Ollama's default is as fast or faster - keeping it"
  $had_dropin && echo "  (the old CPU-only setting stays removed)"
fi
echo "  Results saved to $RESULTS"

# ---------------------------------------------------------------- optional Vulkan ---
if $VULKAN; then
  step "Extra: integrated/other GPUs via llama.cpp Vulkan (report only)"
  sudo apt-get install -y build-essential cmake libcurl4-openssl-dev libvulkan-dev glslc \
    spirv-headers vulkan-tools mesa-vulkan-drivers >/dev/null
  if ! vulkaninfo --summary 2>/dev/null | grep -qi devicename; then
    warn "No Vulkan GPU found - skipping"; exit 0
  fi
  vulkaninfo --summary 2>/dev/null | grep -i devicename | sed 's/^/  /'
  LC="$RUNTIME/llama.cpp"
  [ -d "$LC" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LC"
  cmake -S "$LC" -B "$LC/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$LC/build" --config Release -j "$(nproc)" --target llama-bench >/dev/null
  GGUF=$(ollama show --modelfile "$BENCH_MODEL" | awk '/^FROM \//{print $2}')
  cp "$GGUF" "$RUNTIME/bench.gguf" 2>/dev/null || sudo cp "$GGUF" "$RUNTIME/bench.gguf"
  sudo chown "$USER" "$RUNTIME/bench.gguf"
  sudo systemctl stop ollama
  echo "CPU only (GPU hidden):"
  GGML_VK_VISIBLE_DEVICES="" "$LC/build/bin/llama-bench" -m "$RUNTIME/bench.gguf" -t "$(lscpu -p=CORE 2>/dev/null | grep -v "^#" | sort -u | wc -l)" -p 512 -n 128 -r 2 | tail -n +2
  echo "Whole model on the Vulkan GPU:"
  "$LC/build/bin/llama-bench" -m "$RUNTIME/bench.gguf" -p 512 -n 128 -ngl 99 -r 2 | tail -n +2
  sudo systemctl start ollama
  rm -f "$RUNTIME/bench.gguf"
  echo "If the GPU rows are not clearly faster than the CPU rows, the CPU is the better choice."
  echo "(Ollama does not use most integrated GPUs by default, so this is for information.)"
fi
