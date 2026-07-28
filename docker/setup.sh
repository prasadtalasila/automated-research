#!/usr/bin/env bash
# Builds and starts GROBID as a background service inside the container,
# and sanity-checks the rest of the heavier toolchain (TeX Live, Pandoc).
# Run once per container instance:
#   docker exec -it <container> /usr/local/bin/setup-grobid.sh
#
# NOT executed in this session -- authored against the Dockerfile in this
# directory but unverified (no Docker daemon available on the host this
# was written on). Validate against a real run before relying on it.
set -euo pipefail

GROBID_DIR=/opt/grobid
GROBID_PORT="${GROBID_PORT:-8070}"

if [ ! -x "${GROBID_DIR}/gradlew" ]; then
    echo "GROBID source not found at ${GROBID_DIR} -- check the Dockerfile's fetch step." >&2
    exit 1
fi

echo "Building GROBID (first run only; this step is what's slow/multi-GB) ..."
(cd "${GROBID_DIR}" && ./gradlew clean build -x test)

echo "Starting GROBID in the background on port ${GROBID_PORT} ..."
(cd "${GROBID_DIR}" && nohup ./gradlew run > /var/log/grobid.log 2>&1 &)

echo "Waiting for GROBID to come up ..."
for _ in $(seq 1 60); do
    if curl -fsS "http://localhost:${GROBID_PORT}/api/isalive" > /dev/null 2>&1; then
        echo "GROBID is up: http://localhost:${GROBID_PORT}"
        break
    fi
    sleep 5
done

echo
echo "Checking the rest of the toolchain:"
command -v latexmk >/dev/null && echo "  latexmk:  OK" || echo "  latexmk:  MISSING"
command -v pandoc  >/dev/null && echo "  pandoc:   OK" || echo "  pandoc:   MISSING"
python3 -c "import sentence_transformers" 2>/dev/null && echo "  sentence-transformers: OK" || echo "  sentence-transformers: MISSING"
python3 -c "import chromadb" 2>/dev/null && echo "  chromadb: OK" || echo "  chromadb: MISSING"
python3 -c "import bertopic" 2>/dev/null && echo "  bertopic: OK" || echo "  bertopic: MISSING"

echo
echo "GROBID logs: /var/log/grobid.log"
