#!/usr/bin/env bash
# Start a local SereneDB on 127.0.0.1:7890.
# Linux x86_64: downloads the native binary from the GitHub release.
# Everything else (macOS, Windows, Linux ARM): runs the official serenedb/serenedb Docker image
# (emulated via --platform linux/amd64 on ARM hosts; there is no native arm64 build yet).
set -euo pipefail
cd "$(dirname "$0")/.."
V="${SERENEDB_VERSION:-26.10.1}"
if (echo > /dev/tcp/127.0.0.1/7890) 2>/dev/null; then echo "SereneDB already listening on :7890"; exit 0; fi

if command -v serened >/dev/null || [ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ]; then
  if ! command -v serened >/dev/null; then
    mkdir -p .serenedb
    [ -x ".serenedb/serened" ] || {
      curl -fL -o .serenedb/sdb.tar.gz "https://github.com/serenedb/serenedb/releases/download/v${V}/serenedb-${V}-linux-amd64.tar.gz"
      tar -xzf .serenedb/sdb.tar.gz -C .serenedb --strip-components=3 "serenedb-${V}-linux-amd64/usr/bin/serened"
    }
    BIN=.serenedb/serened
  else BIN=serened; fi
  mkdir -p .serenedb/data
  setsid nohup "$BIN" .serenedb/data --listen=postgres://127.0.0.1:7890 > .serenedb/server.log 2>&1 < /dev/null &
elif command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  PLATFORM_FLAG=""
  case "$(uname -m)" in arm64|aarch64) PLATFORM_FLAG="--platform linux/amd64" ;; esac
  docker rm -f serenedb >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  # mounted at the SAME absolute path as on the host: scripts/load.py embeds host paths
  # (e.g. read_parquet('/abs/host/path/...')) directly into the SQL it sends to the server.
  docker run -d --name serenedb $PLATFORM_FLAG -p 7890:7890 \
    -e SERENEDB_HOST_AUTH_METHOD=trust -v "$(pwd):$(pwd)" serenedb/serenedb:latest >/dev/null
else
  echo "Install SereneDB for your platform (https://serenedb.com/download) or install/start Docker, then re-run."; exit 1
fi

for i in $(seq 1 30); do (echo > /dev/tcp/127.0.0.1/7890) 2>/dev/null && { echo "SereneDB ready on :7890"; exit 0; }; sleep 1; done
echo "SereneDB did not start; see .serenedb/server.log (native) or 'docker logs serenedb' (Docker)"; exit 1
