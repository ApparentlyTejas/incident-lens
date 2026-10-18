#!/usr/bin/env bash
# One command from a clean checkout to a loaded, indexed database:   ./scripts/setup.sh [--sample]
#   --sample : use the 2,000-line Loghub samples (seconds; for development and tests)
#   default  : download BGL (709 MB zip) + OpenStack (58 MB), parse, load the FULL corpus
set -euo pipefail
cd "$(dirname "$0")/.."
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Incident Lens needs Python 3.10+ (the mcp package requires it) - found $PYVER."
  echo "On macOS the default 'python3' is often an older system install; try 'python3.11 scripts/setup.sh' or install a newer Python (e.g. via https://www.python.org/downloads/ or 'brew install python@3.12') and re-run with that interpreter."
  exit 1
fi
python3 -m pip install -q -r requirements.txt || python3 -m pip install -q --break-system-packages -r requirements.txt
./scripts/start_serenedb.sh
mkdir -p data/raw data/processed data/sample
if [ "${1:-}" = "--sample" ]; then
  B=https://raw.githubusercontent.com/logpai/loghub/master
  [ -f data/sample/BGL_2k.log ] || curl -fsSL -o data/sample/BGL_2k.log $B/BGL/BGL_2k.log
  [ -f data/sample/OpenStack_2k.log ] || curl -fsSL -o data/sample/OpenStack_2k.log $B/OpenStack/OpenStack_2k.log
  python3 prep/parse_bgl.py data/sample/BGL_2k.log data/sample/bgl_2k.parquet
  python3 prep/parse_openstack.py data/sample/openstack_2k.parquet data/sample/OpenStack_2k.log
  python3 scripts/load.py --sample
else
  # keep ?download=1 and -L: Zenodo otherwise returns a preview page / redirect
  [ -f data/raw/BGL.zip ] || curl -L -o data/raw/BGL.zip "https://zenodo.org/records/8196385/files/BGL.zip?download=1"
  [ -f data/raw/OpenStack.tar.gz ] || curl -L -o data/raw/OpenStack.tar.gz "https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1"
  mkdir -p data/raw/bgl data/raw/openstack
  unzip -oq data/raw/BGL.zip -d data/raw/bgl
  tar -xzf data/raw/OpenStack.tar.gz -C data/raw/openstack
  BGL_LOG=$(find data/raw/bgl -name '*.log' | head -1)
  OS_LOGS=$(find data/raw/openstack -name '*.log' | sort | tr '\n' ' ')
  OS_LABELS=$(find data/raw/openstack -iname '*label*' | head -1)
  echo "BGL: $BGL_LOG"; echo "OpenStack: $OS_LOGS labels: ${OS_LABELS:-none}"
  python3 prep/parse_bgl.py "$BGL_LOG" data/processed/bgl.parquet
  python3 prep/parse_openstack.py data/processed/openstack.parquet $OS_LOGS ${OS_LABELS:+--labels "$OS_LABELS"}
  python3 scripts/load.py
fi
python3 scripts/gen_tool_docs.py
echo "Done. Connect Claude: see README 'Connect Claude'."
