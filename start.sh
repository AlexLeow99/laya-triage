#!/usr/bin/env bash
# Content Triage Console - launcher for Linux / macOS
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# transformers probes for TensorFlow at import; if TF is installed its
# abseil runtime can deadlock model construction.
export USE_TF=0

if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo
  echo "  [X] Virtual environment not found."
  echo "      Please run:  python3 install.py"
  echo
  exit 1
fi

echo "Starting content triage console..."
echo "  The browser will open by itself once the port is ready."
echo "  URL:  http://127.0.0.1:8100"
echo "  Stop: Ctrl+C, or just close this window."
echo

exec "$HERE/.venv/bin/python" "$HERE/app/server.py"
