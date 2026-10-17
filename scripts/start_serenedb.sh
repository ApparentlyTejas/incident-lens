#!/usr/bin/env bash
# Start a local SereneDB on 127.0.0.1:7890 (Linux x86_64 binary from the GitHub release).
# macOS / Windows / ARM: install per https://serenedb.com/download (Docker works too), then skip this script.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${SERENEDB_VERSION:-26.10.1}"
if (echo > /dev/tcp/127.0.0.1/7890) 2>/dev/null; then echo "SereneDB already listening on :7890"; exit 0; fi
if ! command -v serened >/dev/null; then
  [ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || { echo "Install SereneDB for your platform (https://serenedb.com/download), start it on :7890, then re-run."; exit 1; }
  mkdir -p .serenedb && if [ ! -x ".serenedb/serened" ]; then
    curl -fL -o .serenedb/sdb.tar.gz "https://github.com/serenedb/serenedb/releases/download/v${V}/serenedb-${V}-linux-amd64.tar.gz"
    tar -xzf .serenedb/sdb.tar.gz -C .serenedb --strip-components=3 "serenedb-${V}-linux-amd64/usr/bin/serened"; fi
  BIN=.serenedb/serened
else BIN=serened; fi
mkdir -p .serenedb/data
setsid nohup "$BIN" .serenedb/data --listen=postgres://127.0.0.1:7890 > .serenedb/server.log 2>&1 < /dev/null &
for i in $(seq 1 30); do (echo > /dev/tcp/127.0.0.1/7890) 2>/dev/null && { echo "SereneDB ready on :7890"; exit 0; }; sleep 1; done
echo "SereneDB did not start; see .serenedb/server.log"; exit 1
