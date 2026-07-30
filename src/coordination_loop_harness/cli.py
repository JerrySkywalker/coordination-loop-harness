from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import validate_repository
from .leases import acquire, find_overlaps, list_leases, release, replace
from .runs import init_run, render_attach
from .util import load_json
from .validation import repository_root, validate_json_file


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clh", description="Coordination Loop Harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-run", help="Materialize a durable coordination run bundle")
    init.add_argument("--root", type=_path, default=Path.cwd())
    init.add_argument("--run-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--requested-by", required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--repository", action="append", required=True)

    validate = sub.add_parser("validate", help="Validate one JSON document or the repository")
    validate.add_argument("--root", type=_path, default=Path.cwd())
    validate.add_argument("--file", type=_path)

    lease = sub.add_parser("lease", help="Manage local repository-set leases")
    lease_sub = lease.add_subparsers(dest="lease_command", required=True)
    for name in ("acquire", "replace"):
        item = lease_sub.add_parser(name)
        item.add_argument("--candidate", type=_path, required=True)
        item.add_argument("--lock-root", type=_path, required=True)
        item.add_argument("--repo-root", type=_path, default=Path.cwd())
        if name == "replace":
            item.add_argument("--expected-generation", type=int, required=True)
    inspect = lease_sub.add_parser("inspect")
    inspect.add_argument("--candidate", type=_path, required=True)
    inspect.add_argument("--lock-root", type=_path, required=True)
    listing = lease_sub.add_parser("list")
    listing.add_argument("--lock-root", type=_path, required=True)
    close = lease_sub.add_parser("release")
    close.add_argument("--lease-id", required=True)
    close.add_argument("--lock-root", type=_path, required=True)
    close.add_argument("--expected-generation", type=int, required=True)
    close.add_argument("--outcome-ref", required=True)

    attach = sub.add_parser("render-attach", help="Render an Implementer attach prompt without launching it")
    attach.add_argument("--root", type=_path, default=Path.cwd())
    attach.add_argument("--run-id", required=True)
    attach.add_argument("--lease", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init-run":
            root = args.root
            template_version = (root / "TEMPLATE_VERSION").read_text(encoding="utf-8").strip()
            paths = init_run(
                root,
                run_id=args.run_id,
                title=args.title,
                requested_by=args.requested_by,
                objective=args.objective,
                repositories=args.repository,
                template_version=template_version,
            )
            _print_json({"created": [str(path) for path in paths]})
            return 0

        if args.command == "validate":
            root = repository_root(args.root)
            findings = validate_json_file(args.file, root) if args.file else validate_repository(root)
            _print_json({"ok": not findings, "findings": findings})
            return 0 if not findings else 1

        if args.command == "render-attach":
            output = render_attach(args.root, args.run_id, lease_path=args.lease)
            _print_json({"output": str(output), "process_started": False})
            return 0

        if args.command == "lease":
            if args.lease_command == "acquire":
                path = acquire(args.candidate, args.lock_root, repo_root=args.repo_root)
                _print_json({"lease": str(path), "state": "ACTIVE"})
                return 0
            if args.lease_command == "replace":
                path = replace(
                    args.candidate,
                    args.lock_root,
                    expected_generation=args.expected_generation,
                    repo_root=args.repo_root,
                )
                _print_json({"lease": str(path), "state": "ACTIVE"})
                return 0
            if args.lease_command == "inspect":
                candidate = load_json(args.candidate)
                overlaps = find_overlaps(candidate, args.lock_root, excluding=candidate.get("lease_id"))
                _print_json(
                    {
                        "overlap": bool(overlaps),
                        "findings": [item.__dict__ for item in overlaps],
                    }
                )
                return 1 if overlaps else 0
            if args.lease_command == "list":
                _print_json({"leases": list_leases(args.lock_root)})
                return 0
            if args.lease_command == "release":
                path = release(
                    args.lease_id,
                    args.lock_root,
                    expected_generation=args.expected_generation,
                    outcome_ref=args.outcome_ref,
                )
                _print_json({"lease": str(path), "state": "RELEASED"})
                return 0
    except (ValueError, RuntimeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unhandled command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
