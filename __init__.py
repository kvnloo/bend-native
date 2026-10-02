"""Native Bend integration, distributed independently of Hermes core."""

from __future__ import annotations

from pathlib import Path

from .service import BEND_VERIFY_SCHEMA, BendService
from .commands import run, setup_parser
from .stack.bridge import Stack, setup_parser as stack_parser, run as stack_run
from .stack.observer import _HOOKS


def register(ctx) -> None:
    service = BendService(ctx)
    stack = Stack(ctx)
    for event in (*_HOOKS, 'post_llm_call'):
        ctx.register_hook(event, stack.callback(event))
    ctx.register_cli_command(name='z0', help='z0intelligence stack evidence and runtime',
                             setup_fn=stack_parser, handler_fn=lambda args: stack_run(stack, args))
    ctx.register_skill('z0-stack', Path(__file__).parent / 'skills/z0-stack/SKILL.md',
                       description='Observe Hermes, build source-backed state and use the existing z0 runtime.')
    ctx.on_unload(stack.close)
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
