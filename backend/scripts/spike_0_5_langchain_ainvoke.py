"""Spike 0.5 step 2 — live langchain ainvoke (C4 闸门收尾).

Validates the full chain ViMax's specialists use, against a REAL LLM via
TrainerAI's config:
  1. Load config.yaml, translate ModelConfig -> init_chat_model kwargs (§5.3 v3.2).
  2. init_chat_model(**kwargs) instantiates a working model.
  3. prompt | model | PydanticOutputParser + ainvoke runs end-to-end.

Step 1 (import + signature compat) already passed in
docs/spike-2026-06-22-langchain-compat.md. This script closes C4 with a
real network call. Run from backend/:

    uv run python scripts/spike_0_5_langchain_ainvoke.py [--model-name <name>]

Exit 0 = Go, non-zero = No-Go (print the failure).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


class ExtractedCharacter(BaseModel):
    name: str = Field(description="character identifier")
    description: str = Field(description="physical appearance description")


class ExtractCharactersResponse(BaseModel):
    characters: list[ExtractedCharacter]


SCRIPT_SAMPLE = """
A young woman sits alone at a café table, staring out the window.
She has short black hair and wears a red scarf. She takes a sip of coffee.
"""


def _resolve(value: str) -> str:
    """Resolve $ENV_VAR references in config values."""
    if isinstance(value, str) and value.startswith("$"):
        env_name = value[1:]
        resolved = os.environ.get(env_name)
        if not resolved:
            raise RuntimeError(f"config references env var {env_name!r} which is not set")
        return resolved
    return value


def load_model_kwargs(config_path: Path, model_name: str | None) -> dict:
    """Translate a TrainerAI ModelConfig entry into init_chat_model kwargs.

    This is the §5.3 v3.2 translation (NOT create_chat_model output): TrainerAI
    uses a class path in `use` (e.g. langchain_openai:ChatOpenAI) → map to
    model_provider="openai"; carry model/base_url/api_key.
    """
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    models = cfg.get("models", [])
    if not models:
        raise RuntimeError("no models in config.yaml")
    if model_name:
        entry = next((m for m in models if m.get("name") == model_name), None)
        if entry is None:
            raise RuntimeError(f"model {model_name!r} not found; available: {[m['name'] for m in models]}")
    else:
        entry = models[0]

    use = entry.get("use", "")
    # TrainerAI's `use` is a class path like langchain_openai:ChatOpenAI or
    # deerflow.models.patched_openai:PatchedChatOpenAI. ViMax calls
    # init_chat_model(model_provider="openai", ...) which yields ChatOpenAI.
    if "openai" in use.lower() or "patched" in use.lower():
        model_provider = "openai"
    else:
        model_provider = use.split(":")[-1].replace("Chat", "").lower() or "openai"

    kwargs = {
        "model": entry["model"],
        "model_provider": model_provider,
        "base_url": entry["base_url"],
        "api_key": _resolve(entry["api_key"]),
        "temperature": entry.get("temperature", 0.7),
        # qwen-class models emit markdown unless JSON mode is enforced (spike finding §6).
        "model_kwargs": {"response_format": {"type": "json_object"}},
    }
    print(f"[translate] model_name={entry['name']!r} -> init_chat_model kwargs:")
    print(f"            model_provider={kwargs['model_provider']!r} model={kwargs['model']!r} base_url={kwargs['base_url']!r}")
    return kwargs


async def run(model_name: str | None, config_path: Path) -> None:
    kwargs = load_model_kwargs(config_path, model_name)

    print("\n[1/3] init_chat_model(**kwargs) ...")
    model = init_chat_model(**kwargs)
    print(f"      OK -> {type(model).__name__}")

    print("\n[2/3] build prompt | model | PydanticOutputParser ...")
    parser = PydanticOutputParser(pydantic_object=ExtractCharactersResponse)
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content="Extract characters from the script. Respond ONLY as JSON matching the schema. {format_instructions}"),
            HumanMessage(content=f"<SCRIPT>{SCRIPT_SAMPLE}</SCRIPT>"),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = prompt | model | parser
    print("      OK -> chain constructed")

    print("\n[3/3] chain.ainvoke(...) [real network call] ...")
    result = await chain.ainvoke({})
    print(f"      OK -> parsed {type(result).__name__}")
    for c in result.characters:
        print(f"        - {c.name}: {c.description}")

    print("\n=== C4 Go: init_chat_model + PydanticOutputParser + ainvoke all work end-to-end ===")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-name", default=None, help="config.yaml models[].name to use (default: first)")
    ap.add_argument("--config", default=None, help="path to config.yaml (default: ../config.yaml)")
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        return 2

    import asyncio

    try:
        asyncio.run(run(args.model_name, config_path))
    except Exception as e:
        print(f"\n=== C4 No-Go: {type(e).__name__}: {e} ===", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
