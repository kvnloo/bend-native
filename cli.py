"""Native `hermes bend` commands."""
import json

from .verify_core import BendVerifyError
from .receipts import export_receipt, read_receipt, replay


def setup_parser(parser):
    commands = parser.add_subparsers(dest="bend_command", required=True)
    commands.add_parser("doctor", help="Check the installed Bend CLI without compiling a kernel")
    verify = commands.add_parser("verify", help="Check a project's PROOF.bend and print a JSON receipt")
    verify.add_argument("project_dir")
    verify.add_argument("--proof", default="PROOF.bend", dest="proof_file")
    verify.add_argument("--receipt", help="Create a portable JSON receipt (refuses overwrite)")
    replay_cmd = commands.add_parser("replay", help="Recheck exactly matching inputs and verifier identities")
    replay_cmd.add_argument("receipt_file")
    replay_cmd.add_argument("--project", required=True, dest="project_dir")
    replay_cmd.add_argument("--receipt", help="Create a new receipt for this replay")
    commands.add_parser("last-receipt", help="Print the active profile's most recent receipt")


def run(service, args):
    try:
        if args.bend_command == "doctor":
            result = service.doctor()
            code = 0
        elif args.bend_command == "last-receipt":
            result = service.ctx.state.get("last_receipt")
            if result is None:
                raise BendVerifyError("receipt_not_found", "This profile has no saved verification receipt")
            code = 0
        elif args.bend_command == "replay":
            result = replay(service, read_receipt(args.receipt_file), args.project_dir)
            code = 0 if result["success"] else 1
        else:
            result = service.verify(args.project_dir, args.proof_file)
            code = 0 if result["success"] else 1
        if getattr(args, "receipt", None):
            export_receipt(result, args.receipt)
    except (BendVerifyError, OSError) as exc:
        result = {"success": False, "error": str(exc), "code": getattr(exc, "code", "io_error")}
        code = 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(code)
