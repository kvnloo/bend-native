"""Downstream Bend proof-verification plugin."""

from __future__ import annotations

from .tools import BEND_VERIFY_SCHEMA, check_bend_available, handle_bend_verify


def register(ctx) -> None:
    """Register the minimal Bend verification surface."""
    ctx.register_tool(
        name="bend_verify",
        toolset="bend",
        schema=BEND_VERIFY_SCHEMA,
        handler=handle_bend_verify,
        check_fn=check_bend_available,
        emoji="✓",
    )
