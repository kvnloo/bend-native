"""Native Bend integration, distributed independently of Hermes core."""

from __future__ import annotations

from pathlib import Path

from .service import BEND_VERIFY_SCHEMA, BendService
from .commands import run, setup_parser


def register(ctx) -> None:
    service = BendService(ctx)
    ctx.register_tool(
        name="bend_verify",
        toolset="bend",
        schema=BEND_VERIFY_SCHEMA,
        handler=service.handle,
        emoji="✓",
    )
    ctx.register_cli_command(name="bend", help="Bend proof verification",
                             setup_fn=setup_parser, handler_fn=lambda args: run(service, args))
    ctx.register_skill("workflow", Path(__file__).parent / "skills/bend-workflow/SKILL.md",
                       description="Write Bend laws and proofs, verify edits, and interpret scoped receipts.")
    ctx.on_unload(service.close)
