"""Stage 6: STORM long-form synthesis, retrieval grounded in the local
Chroma index (src/heavy/embed_index.py) instead of STORM's default
web-search retriever.

Needs `knowledge-storm` from docker/requirements-full.txt in a venv, AND
an LLM API key -- same constraint as PaperQA2 (src/heavy/paperqa_answer.py):
no local/offline mode, so this stage can be installed and wired but not
actually executed in this environment. Verified via the clean-failure
path, not a real run.
"""

import os

from src import config


class MissingAPIKey(RuntimeError):
    pass


def _require_api_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise MissingAPIKey(
            "STORM needs an LLM to run -- set ANTHROPIC_API_KEY or "
            "OPENAI_API_KEY and re-run this stage. No key is configured "
            "in this environment, so this stage cannot execute here."
        )


def _local_retriever():
    """A minimal STORM-compatible retrieval module backed by the local
    Chroma index, in place of STORM's default web-search retriever."""
    from knowledge_storm.rm import VectorRM

    from src.heavy import embed_index

    rm = VectorRM(
        collection_name="corpus",
        embedding_model=config.EMBEDDING_MODEL,
        db_path=str(config.CHROMA_DIR),
    )
    return rm


def run(topic: str) -> str:
    _require_api_key()

    from knowledge_storm import STORMWikiRunner, STORMWikiRunnerArguments, STORMWikiLMConfigs

    config.STORM_DIR.mkdir(parents=True, exist_ok=True)
    lm_configs = STORMWikiLMConfigs()
    engine_args = STORMWikiRunnerArguments(output_dir=str(config.STORM_DIR))
    runner = STORMWikiRunner(engine_args, lm_configs, _local_retriever())
    runner.run(topic=topic, do_research=True, do_generate_outline=True,
               do_generate_article=True, do_polish_article=True)
    return str(config.STORM_DIR)
