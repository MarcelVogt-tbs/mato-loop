"""Shipped model-family instructions for the non-delegated Build workflow."""

_BUILD_INSTRUCTIONS = {
    "qwen3.8-27b": (
        "When the user requests file changes and tools are available, use this workflow within runtime limits. "
        "Plan only enough to choose the next small, coherent change. "
        "Do not draft complete files or simulate the whole implementation in reasoning. "
        "Put implementation code directly into valid write or edit calls. "
        "Use successive tool rounds to complete the task; do not wait for another user message between steps. "
        "Inspect tool results before planning dependent changes. "
        "Run a relevant check as soon as a meaningful part is testable. "
        "Preserve the full requested scope; smaller steps do not mean fewer requirements."
    ),
}


def build_model_instructions(model_id: str) -> str:
    name = model_id.rsplit("/", 1)[-1].casefold().replace("_", "-")
    for base, instructions in _BUILD_INSTRUCTIONS.items():
        if name == base or name.startswith((f"{base}-", f"{base}:")):
            return instructions
    return ""
