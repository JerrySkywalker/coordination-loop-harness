from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .audit import record_audit, validate_repository, verify_audit
from .binding import bind_goal
from .blockers import evaluate_blocker
from .bootstrap import bootstrap_repository, sync_plan
from .bundles import seal_bundle, verify_bundle
from .decisions import verify_decision
from .harness_model import validate_harness_model, validate_profile_pack
from .leases import acquire, find_overlaps, list_leases, release, replace
from .repository import verify_repository, verify_template_repository_provenance
from .runs import init_run, render_attach
from .status import transition_status
from .util import load_json
from .validation import repository_root, validate_json_file


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clh", description="Coordination Loop Harness")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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

    harness = sub.add_parser(
        "harness", help="Validate generic Harness Model and Profile Pack contracts"
    )
    harness_sub = harness.add_subparsers(dest="harness_command", required=True)
    harness_validate = harness_sub.add_parser("validate")
    harness_validate.add_argument("--root", type=_path, default=Path.cwd())
    harness_validate.add_argument("--model", type=_path, required=True)
    harness_validate.add_argument("--profile-pack", type=_path)

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

    attach = sub.add_parser(
        "render-attach",
        help="Render an Implementer attach prompt without launching it",
    )
    attach.add_argument("--root", type=_path, default=Path.cwd())
    attach.add_argument("--run-id", required=True)
    attach.add_argument("--lease", type=_path)

    bundle = sub.add_parser("bundle", help="Seal or verify a durable Run Bundle")
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)
    for name in ("seal", "verify"):
        item = bundle_sub.add_parser(name)
        item.add_argument("--root", type=_path, default=Path.cwd())
        item.add_argument("--run-id", required=True)

    bind = sub.add_parser("bind-goal", help="Export a local-only Bound Goal package")
    bind.add_argument("--root", type=_path, default=Path.cwd())
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--repository-root", type=_path, required=True)
    bind.add_argument("--state-root", type=_path)
    bind.add_argument("--expected-origin")
    bind.add_argument("--stable-branch")
    bind.add_argument("--expected-input-sha")
    bind.add_argument("--expected-output-sha")
    bind.add_argument("--lease", type=_path)

    repository = sub.add_parser("repository", help="Verify an exact local repository binding")
    repository_sub = repository.add_subparsers(dest="repository_command", required=True)
    repository_verify = repository_sub.add_parser("verify")
    repository_verify.add_argument("--repository-root", type=_path, required=True)
    repository_verify.add_argument("--expected-origin")
    repository_verify.add_argument("--stable-branch")
    repository_verify.add_argument("--expected-sha")
    repository_verify.add_argument("--local-ref", default="HEAD")
    repository_verify.add_argument("--cached-origin-ref")
    repository_verify.add_argument(
        "--require-detached",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    repository_verify.add_argument("--offline", action=argparse.BooleanOptionalAction, default=True)
    repository_verify.add_argument("--gh-command", default="gh")
    repository_provenance = repository_sub.add_parser(
        "template-provenance",
        help="Verify derived-repository Template provenance using GitHub REST metadata",
    )
    repository_provenance.add_argument("--repository-root", type=_path, required=True)
    repository_provenance.add_argument("--target-repository", required=True)
    repository_provenance.add_argument("--template-repository", required=True)
    repository_provenance.add_argument("--template-exact-sha", required=True)
    repository_provenance.add_argument("--gh-command", default="gh")

    decision = sub.add_parser("decision", help="Verify durable owner authorization")
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    decision_verify = decision_sub.add_parser("verify")
    decision_verify.add_argument("--root", type=_path, default=Path.cwd())
    decision_verify.add_argument("--file", type=_path, required=True)
    decision_verify.add_argument("--run-id", required=True)
    decision_verify.add_argument("--action", required=True)
    decision_verify.add_argument("--lease-id")
    decision_verify.add_argument("--lease-generation", type=int)

    status = sub.add_parser("status", help="Apply a legal optimistic status transition")
    status_sub = status.add_subparsers(dest="status_command", required=True)
    status_transition = status_sub.add_parser("transition")
    status_transition.add_argument("--root", type=_path, default=Path.cwd())
    status_transition.add_argument("--run-id", required=True)
    status_transition.add_argument("--to", required=True)
    status_transition.add_argument("--expected-generation", type=int, required=True)
    status_transition.add_argument("--timestamp", required=True)
    status_transition.add_argument("--checkpoint", required=True)
    status_transition.add_argument("--decision", type=_path)

    blocker = sub.add_parser("blocker", help="Normalize and evaluate a blocker")
    blocker_sub = blocker.add_subparsers(dest="blocker_command", required=True)
    blocker_evaluate = blocker_sub.add_parser("evaluate")
    blocker_evaluate.add_argument("--code", required=True)
    blocker_evaluate.add_argument("--summary", required=True)
    blocker_evaluate.add_argument("--scope", required=True)
    blocker_evaluate.add_argument("--recurrence-count", type=int, required=True)
    blocker_evaluate.add_argument("--retry-limit", type=int, required=True)
    blocker_evaluate.add_argument("--owner-action-required", action="store_true")

    audit = sub.add_parser("audit", help="Record or verify an exact-SHA audit")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_record = audit_sub.add_parser("record")
    audit_record.add_argument("--root", type=_path, default=Path.cwd())
    audit_record.add_argument("--run-id", required=True)
    audit_record.add_argument("--audit-id", required=True)
    audit_record.add_argument("--audit-type", choices=("EXACT_HEAD", "EXACT_MAIN"), required=True)
    audit_record.add_argument("--auditor", required=True)
    audit_record.add_argument("--timestamp", required=True)
    audit_record.add_argument("--audited-sha", required=True)
    audit_record.add_argument("--result", choices=("PASS", "FAIL", "BLOCKED"), required=True)
    audit_record.add_argument("--finding", action="append", default=[])
    audit_record.add_argument("--read-only-asserted", action="store_true")
    audit_record.add_argument("--independently-launched-asserted", action="store_true")
    audit_record.add_argument(
        "--read-only-verified",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    audit_record.add_argument(
        "--independently-launched-verified",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    audit_verify = audit_sub.add_parser("verify")
    audit_verify.add_argument("--root", type=_path, default=Path.cwd())
    audit_verify.add_argument("--file", type=_path, required=True)

    bootstrap = sub.add_parser(
        "bootstrap-repository",
        help="Render a derived repository without touching active run data",
    )
    bootstrap.add_argument("--template-root", type=_path, default=Path.cwd())
    bootstrap.add_argument("--target-root", type=_path, required=True)
    bootstrap.add_argument("--project-name", required=True)
    bootstrap.add_argument("--project-slug", required=True)
    bootstrap.add_argument("--template-repository", required=True)
    bootstrap.add_argument("--template-version", required=True)
    bootstrap.add_argument("--template-sha", required=True)
    bootstrap.add_argument("--target-repository")
    bootstrap.add_argument("--gh-command", default="gh")
    bootstrap.add_argument("--dry-run", action="store_true")
    bootstrap.add_argument("--safe-mode", choices=("preserve-active",))

    template = sub.add_parser("template", help="Plan derived-repository template updates")
    template_sub = template.add_subparsers(dest="template_command", required=True)
    sync = template_sub.add_parser("sync-plan")
    sync.add_argument("--template-root", type=_path, default=Path.cwd())
    sync.add_argument("--derived-root", type=_path, required=True)
    sync.add_argument("--project-name", required=True)
    sync.add_argument("--project-slug", required=True)
    sync.add_argument("--template-version", required=True)
    sync.add_argument("--template-sha", required=True)
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
            findings = (
                validate_json_file(args.file, root) if args.file else validate_repository(root)
            )
            _print_json({"ok": not findings, "findings": findings})
            return 0 if not findings else 1

        if args.command == "harness":
            root = repository_root(args.root)
            model = load_json(args.model)
            findings = validate_harness_model(model, root)
            if args.profile_pack is not None:
                findings.extend(validate_profile_pack(load_json(args.profile_pack), model, root))
            _print_json({"ok": not findings, "findings": findings})
            return 0 if not findings else 1

        if args.command == "render-attach":
            output = render_attach(args.root, args.run_id, lease_path=args.lease)
            _print_json({"output": str(output), "process_started": False})
            return 0

        if args.command == "bundle":
            if args.bundle_command == "seal":
                output = seal_bundle(args.root, args.run_id)
                _print_json({"ok": True, "seal": str(output)})
                return 0
            result = verify_bundle(args.root, args.run_id)
            _print_json(result)
            return 0 if result["ok"] else 1

        if args.command == "bind-goal":
            state_root = args.state_root or args.root / ".coord-local"
            outputs = bind_goal(
                args.root,
                args.run_id,
                repository_root=args.repository_root,
                state_root=state_root,
                expected_origin=args.expected_origin,
                stable_branch=args.stable_branch,
                expected_input_sha=args.expected_input_sha,
                expected_output_sha=args.expected_output_sha,
                lease_path=args.lease,
            )
            _print_json(
                {
                    "ok": True,
                    "outputs": [str(path) for path in outputs],
                    "process_started": False,
                    "local_only": True,
                }
            )
            return 0

        if args.command == "repository":
            if args.repository_command == "verify":
                result = verify_repository(
                    args.repository_root,
                    expected_origin=args.expected_origin,
                    stable_branch=args.stable_branch,
                    expected_sha=args.expected_sha,
                    local_ref=args.local_ref,
                    cached_origin_ref=args.cached_origin_ref,
                    require_detached=args.require_detached,
                    offline=args.offline,
                    gh_command=args.gh_command,
                )
            else:
                result = verify_template_repository_provenance(
                    args.repository_root,
                    target_repository=args.target_repository,
                    template_repository=args.template_repository,
                    template_exact_sha=args.template_exact_sha,
                    gh_command=args.gh_command,
                )
            _print_json(result)
            return 0 if result["ok"] else 1

        if args.command == "decision":
            result = verify_decision(
                args.root,
                args.file,
                run_id=args.run_id,
                action=args.action,
                lease_id=args.lease_id,
                lease_generation=args.lease_generation,
            )
            _print_json(result)
            return 0 if result["ok"] else 1

        if args.command == "status":
            output = transition_status(
                args.root,
                args.run_id,
                target=args.to,
                expected_generation=args.expected_generation,
                timestamp=args.timestamp,
                checkpoint=args.checkpoint,
                decision_path=args.decision,
            )
            _print_json({"ok": True, "status": str(output)})
            return 0

        if args.command == "blocker":
            _print_json(
                evaluate_blocker(
                    code=args.code,
                    summary=args.summary,
                    scope=args.scope,
                    recurrence_count=args.recurrence_count,
                    retry_limit=args.retry_limit,
                    owner_action_required=args.owner_action_required,
                )
            )
            return 0

        if args.command == "audit":
            if args.audit_command == "record":
                outputs = record_audit(
                    args.root,
                    run_id=args.run_id,
                    audit_id=args.audit_id,
                    audit_type=args.audit_type,
                    auditor=args.auditor,
                    timestamp=args.timestamp,
                    audited_sha=args.audited_sha,
                    result=args.result,
                    findings=args.finding,
                    read_only_asserted=args.read_only_asserted,
                    independently_launched_asserted=args.independently_launched_asserted,
                    read_only_verified=args.read_only_verified,
                    independently_launched_verified=args.independently_launched_verified,
                )
                _print_json({"ok": True, "created": [str(path) for path in outputs]})
                return 0
            result = verify_audit(args.root, args.file)
            _print_json(result)
            return 0 if result["ok"] else 1

        if args.command == "bootstrap-repository":
            result = bootstrap_repository(
                args.template_root,
                args.target_root,
                project_name=args.project_name,
                project_slug=args.project_slug,
                template_repository=args.template_repository,
                template_version=args.template_version,
                template_sha=args.template_sha,
                dry_run=args.dry_run,
                safe_mode=args.safe_mode,
                target_repository=args.target_repository,
                gh_command=args.gh_command,
            )
            _print_json(result)
            return 0

        if args.command == "template":
            result = sync_plan(
                args.template_root,
                args.derived_root,
                project_name=args.project_name,
                project_slug=args.project_slug,
                template_version=args.template_version,
                template_sha=args.template_sha,
            )
            _print_json(result)
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
                overlaps = find_overlaps(
                    candidate,
                    args.lock_root,
                    excluding=candidate.get("lease_id"),
                )
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
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unhandled command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
