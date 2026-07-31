""".claude/hooks/citation_gate_hook.py: the PostToolUse hook that
mechanically enforces AGENTS.md's citation gate on every Write/Edit under
content/drafts/ (PR #5). This has zero repo-tracked tests of its own --
its three behavioral fixes (path resolution against the hook's own fixed
location rather than cwd, is_relative_to containment instead of substring
matching, and a malformed-stdin guard) were previously only verified with
ad-hoc pipe-tests during that PR's review cycle.

The hook derives repo_root from its own on-disk location
(Path(__file__).resolve().parent.parent.parent), not from
config.CONTENT_DIR -- so content/drafts/ can't be redirected via
isolated_config the way the rest of this test suite redirects paths;
tests that need a real gated file create one under this actual repo's
(gitignored) content/drafts/ and clean it up afterward. The hook's own
subprocess.run call for `python -m src.citation_gate` doesn't pass
env=, so it inherits whatever env this test process hands to the hook
subprocess -- used here to point the citation_gate child process at an
isolated ledger via CONTENT_DIR instead of this repo's real one.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from src import ledger

from tests.conftest import make_reference

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "citation_gate_hook.py"
REAL_DRAFTS_DIR = REPO_ROOT / "content" / "drafts"


def run_hook(stdin_text: str, env: dict | None = None, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin_text,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def payload(file_path) -> str:
    return json.dumps({"tool_input": {"file_path": str(file_path)}})


def env_for(cfg) -> dict:
    """Env for the hook's child citation_gate process to read the
    isolated ledger `isolated_config` set up in *this* process, instead
    of the real repo's content/ledger.sqlite."""
    return {**os.environ, "CONTENT_DIR": str(cfg.CONTENT_DIR)}


class DraftFile:
    """A real file under this repo's actual (gitignored) content/drafts/
    -- the hook's drafts_dir is hardcoded relative to its own file
    location, so it can't be pointed at a tmp_path."""

    def __init__(self, suffix=".md"):
        REAL_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = REAL_DRAFTS_DIR / f"hook_test_{os.getpid()}_{uuid.uuid4().hex}{suffix}"

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


class TestMalformedStdinGuard:
    """Regression coverage for the fix that used to let a malformed
    PostToolUse payload crash the hook (json.JSONDecodeError/ValueError
    propagating uncaught) instead of failing open."""

    def test_invalid_json_does_not_crash(self):
        result = run_hook("not valid json {{{")
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr == ""

    def test_empty_stdin_does_not_crash(self):
        result = run_hook("")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_json_that_is_not_an_object_does_not_crash(self):
        # Regression: json.load succeeds on valid-but-wrong-shaped JSON
        # (a bare array here), which the original guard's except clause
        # (JSONDecodeError/ValueError only) never caught -- the next line
        # then called .get() on a list and crashed with an uncaught
        # AttributeError instead of failing open.
        result = run_hook("[1, 2, 3]")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_tool_input_that_is_not_an_object_does_not_crash(self):
        # Same class of bug as above, one level deeper: "tool_input"
        # present but not itself an object.
        result = run_hook(json.dumps({"tool_input": "not-an-object"}))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestIgnoredPayloads:
    def test_missing_file_path_is_ignored(self):
        result = run_hook(json.dumps({"tool_input": {}}))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_tool_input_is_ignored(self):
        result = run_hook(json.dumps({}))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_path_outside_drafts_dir_is_ignored(self):
        # A real .md file, real absolute path -- outside content/drafts/
        # is the only thing that should exempt it.
        result = run_hook(payload(REPO_ROOT / "AGENTS.md"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_non_gated_extension_inside_drafts_dir_is_ignored(self):
        with DraftFile(suffix=".py") as path:
            path.write_text("@dataclass\nclass Foo:\n    pass\n")
            result = run_hook(payload(path))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestContainmentCheck:
    """Regression coverage for the is_relative_to fix -- containment must
    be checked against resolved path parts, not a "/content/drafts/"
    substring match (which a payload carrying a relative path would
    silently skip, and which a string like
    "/tmp/content/drafts/../../evil.md" could spoof)."""

    def test_dotdot_traversal_out_of_drafts_dir_is_rejected(self):
        traversal = REPO_ROOT / "content" / "drafts" / ".." / ".." / "AGENTS.md"
        result = run_hook(payload(traversal))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_lookalike_path_elsewhere_on_disk_is_rejected(self, tmp_path):
        # A path that contains the literal substring "content/drafts/"
        # but sits under a completely different directory tree -- a naive
        # string check (e.g. "content/drafts/" in str(path)) would wrongly
        # treat this as a gated draft; is_relative_to against the
        # *resolved* repo_root/content/drafts/ must reject it.
        decoy_file = tmp_path / "not_this_repo" / "content" / "drafts" / "evil.md"
        decoy_file.parent.mkdir(parents=True)
        decoy_file.write_text("[@fabricated_key_not_in_ledger]\n")

        result = run_hook(payload(decoy_file))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestPathResolution:
    """Regression coverage for resolving repo_root from the hook's own
    fixed on-disk location rather than the target path or cwd -- a
    relative "content/drafts/<slug>.md" (no leading slash) must still be
    recognized and gated, invoked from any working directory."""

    def test_relative_path_is_resolved_and_gated_regardless_of_cwd(self, isolated_config, tmp_path):
        with DraftFile() as path:
            path.write_text("No citations in this draft at all.\n")
            rel_path = path.relative_to(REPO_ROOT)
            result = run_hook(payload(rel_path), env=env_for(isolated_config), cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""  # gate ran and passed -- not silently skipped


class TestGateEnforcement:
    """The actual point of the hook: a citation_gate FAIL must come back
    as a blocking decision with a reason naming the bad citekey; a PASS
    must produce no output at all (PostToolUse only acts on the
    "decision": "block" shape)."""

    def test_fabricated_citation_blocks_with_reason(self, isolated_config):
        with DraftFile() as path:
            path.write_text("This claim cites [@totally_fabricated_key_2026].\n")
            result = run_hook(payload(path), env=env_for(isolated_config))

        assert result.returncode == 0  # the hook process itself always exits 0
        response = json.loads(result.stdout)
        assert response["decision"] == "block"
        assert "totally_fabricated_key_2026" in response["reason"]
        assert "Citation gate FAILED" in response["reason"]

    def test_verified_citation_does_not_block(self, isolated_config, ledger_con):
        ledger.upsert_reference(
            ledger_con, make_reference(citekey="smith_real_2024", title="A Real Paper")
        )
        with DraftFile() as path:
            path.write_text("A real, grounded claim [@smith_real_2024].\n")
            result = run_hook(payload(path), env=env_for(isolated_config))

        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_draft_with_no_citations_does_not_block(self, isolated_config):
        with DraftFile() as path:
            path.write_text("Plain prose with no citations at all.\n")
            result = run_hook(payload(path), env=env_for(isolated_config))

        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_tex_extension_is_also_gated(self, isolated_config):
        with DraftFile(suffix=".tex") as path:
            path.write_text(r"This claim cites \citep{totally_fabricated_key_2026}." "\n")
            result = run_hook(payload(path), env=env_for(isolated_config))

        assert result.returncode == 0
        response = json.loads(result.stdout)
        assert response["decision"] == "block"
        assert "totally_fabricated_key_2026" in response["reason"]
