"""Command-line interface for foreground and durable peer-review lifecycles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .authorization import AuthorizationDecision, classify_review
from .broker import (
    cancel_review,
    result_review,
    start_review,
    status_review,
    wait_review,
    worker_review,
)
from .runner import run_review


def _scope_request(
    command: argparse.ArgumentParser,
    *,
    include_platform: bool = False,
) -> None:
    command.add_argument("--provider", choices=["auto", "codex", "claude"], default="auto")
    if include_platform:
        command.add_argument("--platform", choices=["codex", "claude", "claude-code"])
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--request-file", type=Path, required=True)


def _provider_request(
    command: argparse.ArgumentParser,
    *,
    default_timeout: int,
    include_platform: bool = False,
    include_authorization: bool = True,
) -> None:
    _scope_request(command, include_platform=include_platform)
    command.add_argument("--timeout-sec", type=int, default=default_timeout)
    command.add_argument("--run-id")
    if include_authorization:
        command.add_argument("--authorization", choices=["auto", "approved"], default="auto")
        command.add_argument("--approval-scope-sha256")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="peer-review")
    sub = root.add_subparsers(dest="operation", required=True)
    preflight = sub.add_parser(
        "preflight", help="classify standing authorization without packetizing content"
    )
    _scope_request(preflight, include_platform=True)
    preflight.add_argument("--timeout-sec", type=int, default=30)
    run = sub.add_parser("run", help="run one foreground review (hard maximum 240s)")
    _provider_request(run, default_timeout=240, include_platform=True)
    start = sub.add_parser("start", help="accept and detach a durable long review")
    _provider_request(start, default_timeout=1800, include_platform=True)
    for operation in ("status", "result"):
        command = sub.add_parser(operation, help=f"read durable {operation} state")
        command.add_argument("--repo-root", type=Path, required=True)
        command.add_argument("--run-id", required=True)
    wait = sub.add_parser("wait", help="wait for a broker boundary, never raw-file polling")
    wait.add_argument("--repo-root", type=Path, required=True)
    wait.add_argument("--run-id", required=True)
    wait.add_argument("--wait-sec", type=int, default=120)
    wait.add_argument("--until", choices=["terminal", "activity"], default="terminal")
    cancel = sub.add_parser("cancel", help="cancel one owned broker process tree")
    cancel.add_argument("--repo-root", type=Path, required=True)
    cancel.add_argument("--run-id", required=True)
    cancel.add_argument("--grace-sec", type=int, default=10)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    _provider_request(worker, default_timeout=1800, include_authorization=False)
    worker.add_argument("--launch-gate", type=Path, required=True)
    worker.add_argument("--expected-request-sha256", required=True)
    worker.add_argument("--expected-payload-scope-sha256", required=True)
    return root


def _authorization(args: argparse.Namespace) -> AuthorizationDecision:
    return classify_review(
        repo_root=args.repo_root,
        request_file=args.request_file,
        provider=args.provider,
        platform=args.platform,
        timeout_sec=args.timeout_sec if args.operation == "preflight" else 30,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.operation == "preflight":
            output = _authorization(args).as_dict()
        elif args.operation in {"run", "start"}:
            decision = _authorization(args)
            approval_matches = (
                args.authorization == "approved"
                and args.approval_scope_sha256 == decision.approval_scope_sha256
            )
            if args.authorization == "approved" and not approval_matches:
                output = decision.as_dict(operation=args.operation)
                output.update(
                    accepted=False,
                    approval_validation_error=(
                        "approval scope hash is missing or does not match current content"
                    ),
                )
                print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
                return 0
            if decision.status == "approval_required":
                if not approval_matches:
                    output = decision.as_dict(operation=args.operation)
                    output["accepted"] = False
                    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
                    return 0
            authorization_status = (
                "approved"
                if decision.status == "approval_required"
                else "standing_approved"
            )
            review = run_review if args.operation == "run" else start_review
            output = review(
                repo_root=args.repo_root,
                request_file=args.request_file,
                provider=args.provider,
                timeout_sec=args.timeout_sec,
                run_id=args.run_id,
                platform=args.platform,
                expected_request_sha256=decision.request_sha256,
                expected_payload_scope_sha256=decision.payload_scope_sha256,
            )
            output["authorization_status"] = authorization_status
            output["authorization_scope"] = decision.as_dict()["scope"]
            if decision.reasons:
                output["authorization_reason_codes"] = list(decision.reason_codes)
        elif args.operation == "status":
            output = status_review(repo_root=args.repo_root, run_id=args.run_id)
        elif args.operation == "wait":
            output = wait_review(
                repo_root=args.repo_root,
                run_id=args.run_id,
                wait_sec=args.wait_sec,
                until=args.until,
            )
        elif args.operation == "result":
            output = result_review(repo_root=args.repo_root, run_id=args.run_id)
        elif args.operation == "cancel":
            output = cancel_review(
                repo_root=args.repo_root,
                run_id=args.run_id,
                grace_sec=args.grace_sec,
            )
        else:
            output = worker_review(
                repo_root=args.repo_root,
                request_file=args.request_file,
                provider=args.provider,
                timeout_sec=args.timeout_sec,
                run_id=args.run_id,
                launch_gate=args.launch_gate,
                expected_request_sha256=args.expected_request_sha256,
                expected_payload_scope_sha256=args.expected_payload_scope_sha256,
            )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    # A durable peer transport outcome is advisory; invocation/contract errors
    # are the only non-zero results.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
