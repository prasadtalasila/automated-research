"""scripts/full_pipeline.py: the orchestrator -- Docling -> embed ->
BERTopic -> Pandoc/LaTeX. Each stage_* wrapper's ok/partial/
skipped/missing-binary shaping is tested directly against mocked
underlying module calls; main()'s stage-selection and per-stage
exception isolation are tested against a fully mocked STAGE_FUNCS/corpus."""

import sys
import types

import pytest

import scripts.full_pipeline as full_pipeline
from src.heavy import docling_parse, embed_index, render_output, topic_model
from src.heavy.corpus import CorpusDoc


def make_args(**overrides):
    ns = types.SimpleNamespace(
        target="host", stages=",".join(full_pipeline.STAGE_ORDER),
        input=None, output_format="pdf", documentclass="article",
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


class TestStageDocling:
    def test_ok_when_no_errors(self, monkeypatch):
        monkeypatch.setattr(docling_parse, "parse_corpus", lambda docs: {"a": "ok: /x"})
        result = full_pipeline.stage_docling([], make_args())
        assert result["status"] == "ok"

    def test_partial_when_any_error(self, monkeypatch):
        monkeypatch.setattr(docling_parse, "parse_corpus", lambda docs: {"a": "ok: /x", "b": "error: boom"})
        result = full_pipeline.stage_docling([], make_args())
        assert result["status"] == "partial"


class TestStageEmbed:
    def test_ok(self, monkeypatch):
        monkeypatch.setattr(embed_index, "build_index", lambda docs: {"a": 3})
        result = full_pipeline.stage_embed([], make_args())
        assert result == {"status": "ok", "detail": {"a": 3}}


class TestStageBertopic:
    def test_ok_shapes_detail(self, monkeypatch):
        monkeypatch.setattr(
            topic_model, "run_topic_model",
            lambda docs: {"n_docs": 2, "assignments": {"a": -1, "b": -1}, "topic_info": [1, 2, 3]},
        )
        result = full_pipeline.stage_bertopic([], make_args())
        assert result["status"] == "ok"
        assert result["detail"] == {"n_docs": 2, "assignments": {"a": -1, "b": -1}}
        assert "topic_info" not in result["detail"]


class TestStageRender:
    def test_skipped_without_input(self):
        result = full_pipeline.stage_render([], make_args(input=None))
        assert result == {"status": "skipped", "detail": "no --input given"}

    def test_missing_binary(self, monkeypatch):
        def raise_missing(*a, **k):
            raise render_output.MissingBinary("pandoc missing")
        monkeypatch.setattr(render_output, "render", raise_missing)
        result = full_pipeline.stage_render([], make_args(input="draft.md"))
        assert result["status"] == "missing-binary"
        assert "pandoc missing" in result["detail"]

    def test_ok(self, monkeypatch, tmp_path):
        out = tmp_path / "draft.pdf"
        monkeypatch.setattr(render_output, "render", lambda *a, **k: out)
        result = full_pipeline.stage_render([], make_args(input="draft.md"))
        assert result == {"status": "ok", "detail": str(out)}


class TestParseArgs:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["full_pipeline.py"])
        args = full_pipeline.parse_args()
        assert args.target == "host"
        assert args.stages == ",".join(full_pipeline.STAGE_ORDER)
        assert args.input is None

    def test_custom_stages(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["full_pipeline.py", "--stages", "embed,bertopic", "--target", "docker"])
        args = full_pipeline.parse_args()
        assert args.stages == "embed,bertopic"
        assert args.target == "docker"


class TestMain:
    def test_runs_only_selected_stages_and_prints_summary(self, monkeypatch, capsys):
        docs = [CorpusDoc(doc_id="a", citekey="a", source="bib", title="t", pdf_path=None)]
        monkeypatch.setattr(full_pipeline.corpus, "build_corpus", lambda: docs)
        monkeypatch.setattr(sys, "argv", ["full_pipeline.py", "--stages", "docling,embed"])

        called = []
        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "docling", lambda d, a: called.append("docling") or {"status": "ok", "detail": "d"})
        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "embed", lambda d, a: called.append("embed") or {"status": "ok", "detail": "e"})
        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "bertopic", lambda d, a: called.append("bertopic") or {"status": "ok", "detail": "b"})

        rc = full_pipeline.main()
        out = capsys.readouterr().out

        assert rc == 0
        assert called == ["docling", "embed"]  # bertopic not selected, never called
        assert "docling" in out and "ok" in out
        assert "=== Summary ===" in out
        assert "WARNING: unknown stage" not in out  # every selected name is real

    def test_warns_on_unknown_stage(self, monkeypatch, capsys):
        """`--stages grobid` (a stage this pipeline used to have) would
        otherwise be a silent no-op -- main() iterates STAGE_ORDER and
        skips anything unselected, so an unused name never surfaces."""
        docs = [CorpusDoc(doc_id="a", citekey="a", source="bib", title="t", pdf_path=None)]
        monkeypatch.setattr(full_pipeline.corpus, "build_corpus", lambda: docs)
        monkeypatch.setattr(sys, "argv", ["full_pipeline.py", "--stages", "grobid,embed"])
        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "embed", lambda d, a: {"status": "ok", "detail": "e"})

        rc = full_pipeline.main()
        out = capsys.readouterr().out

        assert rc == 0  # a bad stage name warns, it doesn't fail the run
        assert "WARNING: unknown stage(s) grobid" in out
        assert "embed" in out  # the valid stage alongside it still ran

    def test_stage_exception_does_not_abort_other_stages(self, monkeypatch, capsys):
        docs = [CorpusDoc(doc_id="a", citekey="a", source="bib", title="t", pdf_path=None)]
        monkeypatch.setattr(full_pipeline.corpus, "build_corpus", lambda: docs)
        monkeypatch.setattr(sys, "argv", ["full_pipeline.py", "--stages", "docling,embed"])

        def raise_boom(d, a):
            raise RuntimeError("stage exploded")

        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "docling", raise_boom)
        monkeypatch.setitem(full_pipeline.STAGE_FUNCS, "embed", lambda d, a: {"status": "ok", "detail": "e"})

        rc = full_pipeline.main()
        out = capsys.readouterr().out

        assert rc == 0
        assert "error" in out
        assert "stage exploded" in out
        assert "embed" in out  # second stage still ran despite the first raising
