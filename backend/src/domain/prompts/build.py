"""Provider-agnostic prompt dispatch."""

from functools import singledispatch


@singledispatch
def build_prompt(req: object) -> str:
    """Build one provider-facing prompt from a typed prompt request."""
    raise NotImplementedError(f"no prompt builder for {type(req).__name__}")


__all__ = ["build_prompt"]
