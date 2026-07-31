"""scripts/release.py: builds release/automated-research-<version>.zip
from git-tracked files, excluding developer-only material. Uses a real,
throwaway git repo (cheap, and exercises the actual `git ls-files` call
rather than mocking subprocess) rather than the real repo's own tracked
files, so exclusions/inclusions are asserted against a small, controlled
fixture instead of this project's ever-changing file list."""

import subprocess

import pytest

import scripts.release as release


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "x"\nversion = "9.9.9"\n'
    )
    (repo / "README.md").write_text("hello")
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_foo.py").write_text("def test_x(): pass")
    (repo / "DEVELOPER.md").write_text("dev notes")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci")
    (repo / ".gitignore").write_text("content/parsed/\n")
    (repo / "AGENTS.md").write_text("agent guidance")
    (repo / "content" / "drafts").mkdir(parents=True)
    (repo / "content" / "drafts" / "example-tutorial.md").write_text("# Example")
    (repo / "papers" / "pdfs").mkdir(parents=True)
    (repo / "papers" / "pdfs" / "manifest.json").write_text("{}")

    subprocess.run(["git", "add", "-A", "-f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo_dir = make_repo(tmp_path)
    monkeypatch.setattr(release, "REPO_ROOT", repo_dir)
    return repo_dir


class TestGetVersion:
    def test_reads_poetry_version(self, repo):
        assert release.get_version() == "9.9.9"


class TestTrackedFiles:
    def test_excludes_tests_and_developer_md(self, repo):
        paths = release.tracked_files()
        assert "README.md" in paths
        assert "src/foo.py" in paths
        assert "pyproject.toml" in paths
        assert not any(p.startswith("tests/") for p in paths)
        assert "DEVELOPER.md" not in paths

    def test_excludes_github_gitignore_and_agents_md(self, repo):
        paths = release.tracked_files()
        assert not any(p.startswith(".github/") for p in paths)
        assert ".gitignore" not in paths
        assert "AGENTS.md" not in paths

    def test_excludes_tracked_files_under_content_and_papers(self, repo):
        paths = release.tracked_files()
        assert not any(p.startswith("content/") for p in paths)
        assert not any(p.startswith("papers/") for p in paths)


class TestBuildRelease:
    def test_zip_contains_only_non_dev_files(self, repo):
        import zipfile

        zip_path, n_files = release.build_release()

        assert zip_path == repo / "release" / "automated-research-9.9.9.zip"
        assert zip_path.exists()
        assert n_files == 3  # README.md, pyproject.toml, src/foo.py

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert "automated-research-9.9.9/README.md" in names
        assert "automated-research-9.9.9/src/foo.py" in names
        assert not any("tests/" in n for n in names)
        assert not any(n.endswith("DEVELOPER.md") for n in names)

    def test_zip_excludes_github_gitignore_and_agents_md(self, repo):
        import zipfile

        zip_path, _ = release.build_release()

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(".github/" in n for n in names)
        assert not any(n.endswith("/.gitignore") for n in names)
        assert not any(n.endswith("/AGENTS.md") for n in names)

    def test_zip_ships_content_and_papers_as_empty_directories(self, repo):
        import zipfile

        zip_path, _ = release.build_release()

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "automated-research-9.9.9/content/" in names
        assert "automated-research-9.9.9/papers/" in names
        # The directory placeholder is present, but none of the real,
        # per-host tracked files that used to live under it are.
        assert not any(n.startswith("automated-research-9.9.9/content/") and n != "automated-research-9.9.9/content/" for n in names)
        assert not any(n.startswith("automated-research-9.9.9/papers/") and n != "automated-research-9.9.9/papers/" for n in names)

        # Staging directory is cleaned up; only the zip remains under release/.
        assert list((repo / "release").iterdir()) == [zip_path]

    def test_rerunning_overwrites_stale_archive(self, repo):
        release.build_release()
        zip_path, _ = release.build_release()
        assert zip_path.exists()

    def test_leftover_staging_dir_from_a_crashed_run_is_cleared(self, repo):
        """A prior run that died before its own cleanup would leave the
        staging dir behind; build_release must clear it, not merge into it
        or fail on FileExistsError."""
        stale_staging = repo / "release" / "automated-research-9.9.9"
        stale_staging.mkdir(parents=True)
        (stale_staging / "leftover-from-a-crashed-run.txt").write_text("stale")

        zip_path, n_files = release.build_release()

        assert n_files == 3
        assert not stale_staging.exists()


class TestMain:
    def test_main_prints_archive_path_and_returns_zero(self, repo, capsys):
        rc = release.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "automated-research-9.9.9.zip" in out
