#!/usr/bin/env python3
"""Run ONE problem of scicode through TrustedRouter and emit a reproducibility bundle.

OpenEvalz is problem-level. This script deliberately refuses to run more than the
sample you name.
"""
from __future__ import annotations

import argparse, json, os, sys, datetime
from pathlib import Path

from tr.provider import model_string, require_delegated_key

TASK = "inspect_evals/scicode"
HERE = Path(__file__).parent


def _reject_globs(sample_id: str) -> str:
    """Inspect matches --sample-id with fnmatch.

    A glob silently selects many samples and only warns; PrerequisiteError fires
    solely when the filtered set is empty. On a pay-per-problem site that turns
    one purchase into hundreds of runs, so refuse glob metacharacters outright.
    """
    if any(c in sample_id for c in "*?[]"):
        raise SystemExit(
            f"refusing glob-like sample id {sample_id!r}: pass one literal id"
        )
    return sample_id


def emit_bundle(log, args, out: Path) -> dict:
    tpl = json.loads((HERE / "bundle.template.json").read_text())
    tpl["eval"]["task"] = TASK
    tpl["eval"]["sample_id"] = args.sample_id
    tpl["model"]["name"] = args.model
    tpl["model"]["provider"] = "trustedrouter"
    tpl["model"]["wire_format"] = "openai-compatible"
    tpl["model"]["reasoning_effort"] = args.reasoning_effort
    tpl["scaffold"]["id"] = args.scaffold
    tpl["scaffold"]["version"] = 1
    if args.monitor_a:
        tpl["monitors"]["a"]["model"] = args.monitor_a
    if args.monitor_b:
        tpl["monitors"]["b"]["model"] = args.monitor_b
    try:
        tpl["dataset"]["source"] = log.eval.dataset.location or log.eval.dataset.name
        tpl["dataset"]["revision"] = getattr(log.eval.dataset, "revision", None)
    except Exception:
        pass
    tpl["outputs"]["eval_log"] = str(args.log_dir)
    tpl["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out.write_text(json.dumps(tpl, indent=2))
    return tpl


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one scicode problem via TrustedRouter")
    ap.add_argument("--sample-id", required=True, help="a single literal sample id")
    ap.add_argument("--model", required=True, help="e.g. anthropic/claude-sonnet-4")
    ap.add_argument("--scaffold", default="baseline")
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--monitor-a", default=None, help="main monitor: trace + tool calls")
    ap.add_argument("--monitor-b", default=None, help="narrow monitor: tool calls only")
    ap.add_argument("--token-limit", type=int, default=200_000)
    ap.add_argument("--log-dir", default="./logs")
    args = ap.parse_args()

    _reject_globs(args.sample_id)
    require_delegated_key()

    from inspect_ai import eval as inspect_eval

    # NOTE: --cost-limit is NOT used, and must not be. Inspect only records cost
    # when its pricing table resolves the model, and SERVICE_PREFIXES is
    # {"azure","bedrock","vertex"} — so trustedrouter/<model> never resolves and
    # the cap silently never binds. The real cap is the delegated key's
    # limit_microdollars, enforced server-side by TrustedRouter.
    logs = inspect_eval(
        TASK,
        model=model_string(args.model),
        sample_id=args.sample_id,
        token_limit=args.token_limit,
        log_dir=args.log_dir,
    )
    if not logs:
        print("no log produced", file=sys.stderr)
        return 1

    log = logs[0]
    n = len(log.samples or [])
    if n != 1:
        print(f"WARNING: expected exactly 1 sample, got {n}", file=sys.stderr)

    bundle = emit_bundle(log, args, HERE / "bundle.json")
    print(json.dumps({
        "status": log.status,
        "samples": n,
        "sample_id": args.sample_id,
        "model": args.model,
        "bundle": "bundle.json",
    }, indent=2))
    return 0 if log.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
