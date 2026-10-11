#!/usr/bin/env bash
# Script 00: read-only check of this computer. Changes nothing.
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 00: hardware check (nothing is changed)"

cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')
threads=$(nproc)
echo "  CPU: $cpu ($threads threads)"
arch=$(uname -m)
if [ "$arch" = "aarch64" ]; then ok "ARM64 CPU (supported)"
elif grep -qw avx2 /proc/cpuinfo; then ok "CPU supports AVX2 (needed for fast AI on CPU)"
else warn "CPU has no AVX2 - models will run, but slowly"; fi

if command -v apt-get >/dev/null 2>&1; then ok "apt found (Ubuntu/Debian family - supported)"
else fail "These scripts need Ubuntu or Debian (apt). Other distros need manual changes in script 01."; fi

if [ "$RAM_GB" -lt 7 ]; then
  fail "RAM: ${RAM_GB} GB - at least 8 GB is needed"
elif [ "$RAM_GB" -lt 28 ]; then
  warn "RAM: ${RAM_GB} GB - the agent will use a small model (${MODEL_MAIN}); 32 GB+ allows the much better 30B model"
else
  ok "RAM: ${RAM_GB} GB -> agent model: ${MODEL_MAIN}"
fi
echo "         planner model: ${MODEL_PLANNER}  (all models are downloaded by script 02)"

if lspci 2>/dev/null | grep -qiE 'nvidia|radeon rx|arc a'; then
  ok "Graphics card found. Ollama uses NVIDIA/AMD GPUs automatically once their drivers are installed (much faster)."
else
  echo "  GPU: none suitable - everything runs on the CPU (normal for mini PCs and most laptops)"
fi

# disk needed = all models (agent + planner + fast + embeddings) plus room for apps
case "$MODEL_MAIN:$MODEL_PLANNER" in
  *30b*:*30b*) need_gb=60 ;;
  *30b*:*)     need_gb=40 ;;
  *)           need_gb=20 ;;
esac
root_free=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if mountpoint -q /data; then
  data_free=$(df -BG --output=avail /data | tail -1 | tr -dc '0-9')
  ok "/data is mounted (${data_free} GB free) - models will be stored there"
elif [ "$root_free" -ge "$need_gb" ]; then
  ok "Free space on /: ${root_free} GB (enough)"
else
  warn "Only ${root_free} GB free on / and no /data disk. You need about ${need_gb} GB. See docs/GUIDE.md step 1.2"
fi

nic=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
if [ -n "$nic" ]; then
  speed=$(cat "/sys/class/net/$nic/speed" 2>/dev/null || echo "?")
  errs=$(cat "/sys/class/net/$nic/statistics/rx_errors" 2>/dev/null || echo 0)
  echo "  Network: $nic at ${speed} Mbit/s, receive errors: $errs"
  [ "$speed" = "100" ] && warn "Link is 100 Mbit/s. Fine for daily use; downloads will be slower. A Cat5e/Cat6 cable gives 1000."
  [ "${errs:-0}" -gt 100 ] 2>/dev/null && warn "Many network errors - the cable may be damaged."
fi

t=$(cpu_temp)
[ -n "$t" ] && echo "  CPU temperature now: ${t} C"

. /etc/os-release 2>/dev/null && echo "  OS: ${PRETTY_NAME:-unknown}"
next "bash scripts/01_install_base.sh"
