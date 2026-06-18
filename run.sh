#!/usr/bin/env bash
#
# One-shot launcher: set up the environment, run the tests, and start the Streamlit UI.
#
#   ./run.sh                 # full: venv + deps + ollama check + tests + UI
#   ./run.sh --skip-tests    # skip the test run
#   ./run.sh --no-ui         # set up and test only (don't launch the UI)
#   PORT=8600 ./run.sh       # serve the UI on a different port
#   OLLAMA_MODEL=qwen2.5-coder:7b ./run.sh
#
set -euo pipefail

# Always operate from the repo root (this script's directory).
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- config (override via env) -------------------------------------------
PORT="${PORT:-8765}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:3b}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL OLLAMA_BASE_URL

RUN_TESTS=1
START_UI=1
for arg in "$@"; do
  case "$arg" in
    --skip-tests) RUN_TESTS=0 ;;
    --no-ui)      START_UI=0 ;;
    -h|--help)    sed -n '3,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

# --- 1. virtualenv -------------------------------------------------------
say "Setting up virtualenv (.venv)"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# --- 2. dependencies -----------------------------------------------------
say "Installing dependencies (editable + ui extra)"
pip install --quiet --upgrade pip
pip install --quiet -e ".[ui]"

# --- 3. GitHub token (optional; needed for --pr and submitting) ----------
if [ -z "${GITHUB_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  if gh auth token >/dev/null 2>&1; then
    GITHUB_TOKEN="$(gh auth token)"
    export GITHUB_TOKEN
    say "Using GITHUB_TOKEN from gh CLI"
  fi
fi
[ -z "${GITHUB_TOKEN:-}" ] && echo "(no GITHUB_TOKEN — public PR reads work; submitting/private needs one)"

# --- 4. Ollama: daemon + model ------------------------------------------
say "Checking Ollama at $OLLAMA_BASE_URL"
if ! curl -sf -m 3 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    echo "Ollama not responding; starting 'ollama serve' in the background…"
    nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
    for _ in $(seq 1 20); do
      curl -sf -m 2 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1 && break
      sleep 0.5
    done
  fi
fi
if ! curl -sf -m 3 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  echo "WARNING: Ollama is not reachable at $OLLAMA_BASE_URL — the review will fail until it's up." >&2
elif ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OLLAMA_MODEL"; then
  say "Pulling model $OLLAMA_MODEL (first run only)"
  ollama pull "$OLLAMA_MODEL"
else
  echo "Model $OLLAMA_MODEL is available."
fi

# --- 5. tests ------------------------------------------------------------
if [ "$RUN_TESTS" -eq 1 ]; then
  say "Running tests"
  PYTHONPATH=src python -m pytest -q
fi

# --- 6. launch UI --------------------------------------------------------
if [ "$START_UI" -eq 1 ]; then
  # Free the port if a previous instance is still bound to it.
  if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
    echo "Port $PORT in use; stopping the existing process."
    kill "$(lsof -ti "tcp:$PORT")" 2>/dev/null || true
    sleep 1
  fi
  say "Starting Streamlit UI on http://localhost:$PORT  (Ctrl-C to stop)"
  exec env PYTHONPATH=src streamlit run src/code_review_agents/ui/streamlit_app.py \
    --server.port "$PORT" --browser.gatherUsageStats false
else
  say "Setup complete (UI not started)."
fi
