"""src/config.py: env-var overrides, config.toml defaults, and the two
pure helpers (_get/_get_float) that implement the override precedence."""

import importlib
import os

import pytest

from src import config


class TestGetHelpers:
    def test_env_var_wins_over_toml(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"section": {"key": "from-toml"}})
        monkeypatch.setenv("MY_VAR", "from-env")
        assert config._get("MY_VAR", "section", "key", default="fallback") == "from-env"

    def test_falls_back_to_toml_path(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"section": {"key": "from-toml"}})
        monkeypatch.delenv("MY_VAR", raising=False)
        assert config._get("MY_VAR", "section", "key", default="fallback") == "from-toml"

    def test_default_when_toml_path_missing(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"section": {}})
        monkeypatch.delenv("MY_VAR", raising=False)
        assert config._get("MY_VAR", "section", "key", default="fallback") == "fallback"

    def test_default_when_toml_path_not_a_dict(self, monkeypatch):
        # "section" resolves to a string, not a dict -- the next path
        # segment ("key") can't be looked up in it.
        monkeypatch.setattr(config, "_toml", {"section": "not-a-dict"})
        monkeypatch.delenv("MY_VAR", raising=False)
        assert config._get("MY_VAR", "section", "key", default="fallback") == "fallback"

    def test_default_when_leaf_is_not_a_string(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"section": {"key": 123}})
        monkeypatch.delenv("MY_VAR", raising=False)
        assert config._get("MY_VAR", "section", "key", default="fallback") == "fallback"

    def test_float_env_var_wins(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"heavy": {"timeout": 3.0}})
        monkeypatch.setenv("MY_TIMEOUT", "9.5")
        assert config._get_float("MY_TIMEOUT", "heavy", "timeout", default=1.0) == 9.5

    def test_float_falls_back_to_toml_number(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"heavy": {"timeout": 3}})
        monkeypatch.delenv("MY_TIMEOUT", raising=False)
        assert config._get_float("MY_TIMEOUT", "heavy", "timeout", default=1.0) == 3.0

    def test_float_default_when_missing(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"heavy": {}})
        monkeypatch.delenv("MY_TIMEOUT", raising=False)
        assert config._get_float("MY_TIMEOUT", "heavy", "timeout", default=1.5) == 1.5

    def test_float_default_when_not_a_dict(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"heavy": "nope"})
        monkeypatch.delenv("MY_TIMEOUT", raising=False)
        assert config._get_float("MY_TIMEOUT", "heavy", "timeout", default=1.5) == 1.5

    def test_float_default_when_bool_in_toml(self, monkeypatch):
        # bool is a subclass of int in Python -- must not be silently
        # accepted as a numeric timeout.
        monkeypatch.setattr(config, "_toml", {"heavy": {"timeout": True}})
        monkeypatch.delenv("MY_TIMEOUT", raising=False)
        assert config._get_float("MY_TIMEOUT", "heavy", "timeout", default=1.5) == 1.5

    @pytest.mark.parametrize("raw,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        (" true ", True),
        # The whole point of _get_bool: bool("false") is True, so a plain
        # cast would make every documented way of switching a setting off
        # via the environment switch it on instead.
        ("0", False), ("false", False), ("FALSE", False), ("no", False),
        ("off", False), ("", False),
    ])
    def test_bool_env_var_parses_words_not_truthiness(self, monkeypatch, raw, expected):
        monkeypatch.setattr(config, "_toml", {"heavy": {"flag": not expected}})
        monkeypatch.setenv("MY_FLAG", raw)
        assert config._get_bool("MY_FLAG", "heavy", "flag", default=not expected) is expected

    def test_bool_falls_back_to_toml(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"heavy": {"flag": False}})
        monkeypatch.delenv("MY_FLAG", raising=False)
        assert config._get_bool("MY_FLAG", "heavy", "flag", default=True) is False

    def test_bool_default_when_missing_or_wrong_type(self, monkeypatch):
        monkeypatch.delenv("MY_FLAG", raising=False)
        monkeypatch.setattr(config, "_toml", {"heavy": {}})
        assert config._get_bool("MY_FLAG", "heavy", "flag", default=True) is True
        # A non-bool in the toml is ignored rather than coerced.
        monkeypatch.setattr(config, "_toml", {"heavy": {"flag": "yes"}})
        assert config._get_bool("MY_FLAG", "heavy", "flag", default=False) is False
        monkeypatch.setattr(config, "_toml", {"heavy": "nope"})
        assert config._get_bool("MY_FLAG", "heavy", "flag", default=True) is True


class TestRealConfigToml:
    """Sanity-checks the constants computed from this repo's actual
    config.toml + ambient environment at real import time."""

    def test_bib_file_path_under_repo_root(self):
        assert config.BIB_FILE_PATH == config.REPO_ROOT / "papers" / "bibliography.bib"

    def test_content_dir_layout(self):
        assert config.CONTENT_DIR == config.REPO_ROOT / "content"
        assert config.PARSED_DIR == config.CONTENT_DIR / "parsed"
        assert config.LEDGER_PATH == config.CONTENT_DIR / "ledger.sqlite"
        assert config.RETRIEVAL_INDEX_PATH == config.CONTENT_DIR / "retrieval_index.json"

    def test_embedding_model_default(self):
        assert config.EMBEDDING_MODEL == "sentence-transformers/all-mpnet-base-v2"


class TestGetWorkers:
    """[parser].workers is the one setting that isn't a plain str/float/
    bool: it's a positive int OR the literal "auto", and a wrong value
    has to be rejected at load rather than turning into a nonsense pool
    size much later."""

    @pytest.fixture(autouse=True)
    def _toml(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"parser": {}})

    def test_missing_key_uses_default(self):
        assert config._get_workers("W", "parser", "workers", default=1) == 1

    def test_int_from_toml(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"parser": {"workers": 8}})
        assert config._get_workers("W", "parser", "workers", default=1) == 8

    def test_auto_from_toml(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"parser": {"workers": "auto"}})
        assert config._get_workers("W", "parser", "workers", default=1) == "auto"

    @pytest.mark.parametrize("raw", ["auto", "AUTO", " Auto "])
    def test_auto_is_case_and_space_insensitive(self, monkeypatch, raw):
        monkeypatch.setenv("W", raw)
        assert config._get_workers("W", "parser", "workers", default=1) == "auto"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setattr(config, "_toml", {"parser": {"workers": 8}})
        monkeypatch.setenv("W", "3")
        assert config._get_workers("W", "parser", "workers", default=1) == 3

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_below_one_is_rejected(self, monkeypatch, raw):
        monkeypatch.setenv("W", raw)
        with pytest.raises(ValueError, match="positive integer"):
            config._get_workers("W", "parser", "workers", default=1)

    def test_nonsense_string_is_rejected(self, monkeypatch):
        monkeypatch.setenv("W", "lots")
        with pytest.raises(ValueError, match="positive integer"):
            config._get_workers("W", "parser", "workers", default=1)

    def test_bool_is_rejected(self, monkeypatch):
        """TOML `workers = true` parses as a bool, and bool is an int
        subclass in Python -- so without an explicit check this would
        silently mean "1 worker" instead of being called out."""
        monkeypatch.setattr(config, "_toml", {"parser": {"workers": True}})
        with pytest.raises(ValueError, match="positive integer"):
            config._get_workers("W", "parser", "workers", default=1)


class TestModuleReloadWithEnvOverrides:
    """Full module-level reload, to cover the constant-computation lines
    themselves (BIB_FILE_PATH = REPO_ROOT / _get(...), etc.) under a real
    env-var override -- not just the _get helper in isolation."""

    @pytest.fixture(autouse=True)
    def _restore_config_after(self):
        yield
        # Reload once more with a clean environment so later test modules
        # see the real repo config.toml, not whatever this test overrode.
        #
        # CONFIG_PATH is cleared here rather than left to monkeypatch's own
        # teardown: fixture finalisation order depends on which fixtures
        # requested monkeypatch, so this reload can run while a test's
        # deliberately-bogus CONFIG_PATH is still set -- and since v1.0.0
        # that is a hard FileNotFoundError, turning an unrelated passing
        # test into a teardown error.
        os.environ.pop("CONFIG_PATH", None)
        importlib.reload(config)

    def test_bib_file_env_override(self, monkeypatch):
        monkeypatch.setenv("BIB_FILE", "/tmp/other.bib")
        importlib.reload(config)
        assert config.BIB_FILE_PATH == config.REPO_ROOT / "/tmp/other.bib"

    def test_parser_ocr_defaults_off(self, monkeypatch):
        monkeypatch.delenv("PARSER_OCR", raising=False)
        importlib.reload(config)
        assert config.PARSER_OCR is False

    def test_parser_ocr_env_override(self, monkeypatch):
        monkeypatch.setenv("PARSER_OCR", "true")
        importlib.reload(config)
        assert config.PARSER_OCR is True

    def test_embedding_model_env_override(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL", "sentence-transformers/other-model")
        importlib.reload(config)
        assert config.EMBEDDING_MODEL == "sentence-transformers/other-model"

    def test_missing_config_file_names_the_fix(self, monkeypatch, tmp_path):
        """config.toml is gitignored per-host data, so "it isn't there"
        is the *normal* state of a fresh clone -- the first thing anyone
        hits. Deliberately a hard failure rather than a silent fallback
        to config.toml.example: a host quietly running settings its owner
        never chose is worse than one that won't start. The message has
        to carry the actual command, since the file it names is not
        obviously related to the traceback that brought you here."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.toml"))
        with pytest.raises(FileNotFoundError) as excinfo:
            importlib.reload(config)
        message = str(excinfo.value)
        assert "cp config.toml.example config.toml" in message
        assert str(tmp_path / "config.toml") in message

    def test_custom_config_path(self, monkeypatch, tmp_path):
        custom_toml = tmp_path / "custom.toml"
        custom_toml.write_text(
            '[bib]\npath = "elsewhere.bib"\n[heavy]\nembedding_model = "custom/model"\n'
        )
        monkeypatch.setenv("CONFIG_PATH", str(custom_toml))
        importlib.reload(config)
        assert config.CONFIG_PATH == custom_toml
        assert config.BIB_FILE_PATH == config.REPO_ROOT / "elsewhere.bib"
        assert config.EMBEDDING_MODEL == "custom/model"
