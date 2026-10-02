"""Native `hermes bend` commands."""
import json

from .verify_core import BendVerifyError


def setup_parser(parser):
    commands = parser.add_subparsers(dest="bend_command", required=True)
    commands.add_parser("doctor", help="Check the installed Bend CLI without compiling a kernel")
    verify = commands.add_parser("verify", help="Check a project's PROOF.bend and print a JSON receipt")
    verify.add_argument("project_dir")
    verify.add_argument("--proof", default="PROOF.bend", dest="proof_file")


def run(service, args):
    try:
        if args.bend_command == "doctor":
            result = service.doctor()
            code = 0
        else:
            result = service.verify(args.project_dir, args.proof_file)
            code = 0 if result["success"] else 1
    except (BendVerifyError, OSError) as exc:
        result = {"success": False, "error": str(exc), "code": getattr(exc, "code", "io_error")}
        code = 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(code)
