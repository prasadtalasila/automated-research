# Building and running GROBID standalone

GROBID gives `src/heavy/grobid_extract.py` bibliographic-quality header
and reference extraction (for documents under `config.toml`'s
`[source_pdfs].dir`, which -- unlike bib-sourced ones -- have no metadata
of their own; see that module's docstring). This is the step-by-step
recipe for building and running it
directly on a bare host. There's nothing Docker-exclusive about GROBID
itself -- `docker/setup.sh` just runs this same recipe inside a
container for hosts that don't have root or a JDK 21 available directly
(see [DOCKER.md](DOCKER.md)).

**What's actually verified vs. not:** a GROBID 0.9.0 build done by hand
(fetch, `./gradlew clean build -x test`, unzip+run the standalone
distribution) answers `/api/isalive` and `/api/health` at
`http://localhost:8070`, matching `config.toml`'s `grobid_url` default,
and has successfully extracted a real TEI header from a test PDF.
`grobid_extract.is_available()` was re-verified against that live
service from a real `.venv-full` and correctly returned `True`.

`scripts/install_full_pipeline.sh grobid` (Step 2 below) automates the
*build* half of that recipe -- fetch, `./gradlew clean build -x test`,
unzip the standalone distribution -- and has itself been run end-to-end
(not just written and assumed to work); see its own script comments for
the exact failure modes it now guards against (a `unzip` flag that
silently extracted nothing; a JDK version mismatch). It does **not**
start the service -- that half (Step 3 below, `./gradlew run`) was run by
hand, not through the script, and stays a manual step here.

## Prerequisites

GROBID needs a **JDK**, not a JRE -- it compiles Kotlin/Java from
source, and that needs `javac`. And it must be **version 21
specifically, not whatever's newest**: GROBID's `build.gradle` pins a
Java 21 toolchain, and its bundled Kotlin compiler (2.0.21) throws
`IllegalArgumentException: 25.0.3` trying to parse a JDK 25 version
string -- it predates JDK 25's existence.

## Step 1: install the JDK and build tools

```bash
bash scripts/install_full_pipeline.sh os-deps
```

Installs JDK 21 (and everything else `os-deps` covers -- TeX Live,
Pandoc, poppler-utils, Poetry) via apt. Needs root; skip this step if
you already have a JDK 21 available as the default `java`.

## Step 2: fetch and build GROBID

```bash
bash scripts/install_full_pipeline.sh grobid
```

Fetches **[grobidOrg/grobid](https://github.com/grobidOrg/grobid)** (the
authoritative GROBID repository) at the pinned `GROBID_VERSION` (default
`0.9.0`), extracts it, and builds it (`./gradlew clean build -x test`,
which also produces the standalone distribution zips). This step is
**multi-GB and multi-minute** (Gradle downloading the Maven dependency
graph, then compiling) -- expect it to take a while the first time.

Override where it's fetched to with `GROBID_DIR` (defaults to
`$HOME/grobid-<version>` on a bare host; `docker/Dockerfile` uses
`/opt/grobid`) and the version with `GROBID_VERSION`, e.g.:

```bash
GROBID_VERSION=0.9.0 GROBID_DIR=/opt/grobid bash scripts/install_full_pipeline.sh grobid
```

## Step 3: start it

```bash
(cd "${GROBID_DIR:-$HOME/grobid-0.9.0}" && ./gradlew run)
```

This blocks in the foreground (Ctrl-C to stop). Run it in the
background, or in a separate terminal/tmux pane, if you want to keep
using this shell:

```bash
(cd "${GROBID_DIR:-$HOME/grobid-0.9.0}" && nohup ./gradlew run > /tmp/grobid.log 2>&1 &)
```

## Step 4: verify it's up

```bash
curl -fsS http://localhost:8070/api/isalive
```

Should print `true`. Also check from Python, since that's what
`src/heavy/grobid_extract.py` actually calls:

```bash
.venv-full/bin/python -c "
from src.heavy import grobid_extract
print('is_available:', grobid_extract.is_available())
"
```

Match `config.toml`'s `[heavy].grobid_url` (default
`http://localhost:8070`) if you started GROBID on a different host or
port.

## Troubleshooting

**"java is version X, but GROBID needs exactly JDK 21"** --
`install_full_pipeline.sh grobid` checks the *default* `java` up front
and exits with this message rather than failing deep inside the Kotlin
compiler. On a host with multiple JDKs installed where 21 isn't the
default, a working JDK 21 may still be present but not wired up as
`java`. Either of these fixes it (don't need both):

```bash
sudo update-alternatives --config java   # changes the system default
echo 'org.gradle.java.home=/usr/lib/jvm/java-21-openjdk-amd64' >> "$GROBID_DIR/gradle.properties"   # scoped to this GROBID checkout only (adjust the path to your actual JDK 21 install)
```

**A stale Gradle or Kotlin daemon keeps using the wrong JDK** -- if a
Gradle daemon or the separate long-lived Kotlin compiler daemon (`ps aux
| grep -i kotlin`) already started under the wrong JDK before you fixed
the above, neither one picks up a JDK change on its own. Stop both and
retry:

```bash
(cd "$GROBID_DIR" && ./gradlew --stop)
pkill -f 'KotlinCompileDaemon' || true
```

**`unzip` extracted nothing / `GROBID_DIR` ends up empty** -- fixed in
this repo's `install_full_pipeline.sh`: an earlier version passed a
`tar`-only flag (`--strip-components`) to `unzip`, which silently
treated it as a no-match filename filter rather than an error, leaving
`GROBID_DIR` empty with no visible failure. If you're running an older
copy of this script, update it rather than working around the symptom.

## What's next

Once GROBID is reachable, `scripts/full_pipeline.py --stages grobid`
(or the full default stage list) will pick it up automatically --
`src/heavy/grobid_extract.py` self-probes with `is_available()` before
every call and reports `skipped` honestly if it isn't running, rather
than hanging or stack-tracing. See the main [README](README.md)'s
["The heavy pipeline"](README.md#the-heavy-pipeline) section.
