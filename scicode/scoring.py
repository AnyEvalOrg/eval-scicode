"""Main-problem all-steps verdict with template harness-failure semantics."""
import asyncio
import json
import re
from typing import NamedTuple
from inspect_ai.scorer import CORRECT, INCORRECT, Score, accuracy, scorer
from inspect_ai.util import sandbox
from .dataset import load_records
from .execution import execution_request
from .solver import composed_code, generated_steps
from .publication import private_grading
from .sandbox_runner import CLEANUP_COMMAND, QUIESCENCE_COMMAND, SETUP, RUNNER
from .receipts import verify_receipt, receipt_failure
from .sandbox_state import sandbox_failure


class StepResult(NamedTuple):
    passed: bool
    reason: str
    terminal: bool = False


async def run_payload(env, payload):
    request = json.dumps(payload)
    # One credential-dropped child per subproblem, one upstream timeout for all
    # its tests, with extra time for root comparison and authenticated signing.
    deadline = payload['timeout'] + 30
    receipt = None
    signed_failure = None
    kernel_failure = None
    cleanup_failed = False
    cleanup_after = 0
    try:
        with private_grading(env) as private:
            try:
                async with asyncio.timeout(10):
                    setup = await private.exec(
                        ["timeout", "-s", "KILL", "5s",
                         "/usr/local/bin/python3", "-I", "-c", SETUP],
                        cwd="/", input=request, timeout=5, timeout_retry=False,
                    )
                if setup.returncode != 0:
                    raise RuntimeError("Sandbox setup failed")
                setup_receipt = json.loads(setup.stdout)
                work = setup_receipt["cwd"]
                key = bytes.fromhex(setup_receipt["key"])
                if len(key) != 32:
                    raise RuntimeError("Invalid setup key")
                if not re.fullmatch(r"/tmp/scicode-[a-zA-Z0-9_-]+", work):
                    raise RuntimeError("Invalid setup directory")
                # If exec returns early without a receipt, wait through
                # the outer deadline before sweeping: the supervisor may
                # still be starting. This uses the host monotonic clock.
                cleanup_after = asyncio.get_running_loop().time() + deadline + 5
                try:
                    async with asyncio.timeout(deadline + 5):
                        result = await private.exec(
                            ["timeout", "-s", "KILL", f"{deadline}s",
                             "/usr/local/bin/python3", "-I", "-c", RUNNER, work],
                            cwd="/", timeout=deadline, timeout_retry=False,
                        )
                    receipt = verify_receipt(result.stdout, key)
                    if receipt is not None and receipt["cwd"] == work:
                        # Decide authenticated failure before independent cleanup.
                        signed_failure = receipt_failure(receipt)
                        # Authenticated completion means no later spawn;
                        # sweep immediately before starting the next test.
                        cleanup_after = 0
                    else:
                        receipt = None
                except Exception:
                    receipt = None
                if receipt is None:
                    kernel_failure = await sandbox_failure(private)
                    if kernel_failure is not None:
                        cleanup_after = 0
                    # A Running pod, even after timeout -s KILL returns 137,
                    # cannot authenticate a "supervisor deadline exceeded"
                    # verdict: exec transport stalls and infrastructure CPU
                    # starvation look identical. Keep these harness errors
                    # so infrastructure cannot enter the pass rate. Candidate
                    # timeouts normally yield signed failures; a missing
                    # receipt with inconclusive kernel evidence withholds the
                    # entire run, never silently drops a sample from its rate.
            finally:
                # A separate sandbox exec, never the candidate's parent or
                # session, enforces cleanup on EVERY path (also setup failure).
                cleanup = asyncio.create_task(cleanup_candidate(private, cleanup_after))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
                except Exception:
                    # The pod is per-sample and discarded afterwards; there is
                    # no reuse across samples. Cleanup cannot erase a signed
                    # failure or turn candidate misbehaviour into a harness error.
                    if receipt is None and kernel_failure is None:
                        raise
                    cleanup_failed = True
    except Exception:
        # Provider exceptions may embed stdin or captured output. Do not
        # allow them (or their exception chain) into an Inspect error event.
        raise RuntimeError("Private sandbox operation failed; details withheld.") from None
    # Neither success nor returncode from the run provider is a verdict channel.
    if receipt is None:
        if kernel_failure is not None:
            return StepResult(False, kernel_failure, True)
        raise RuntimeError("Private sandbox operation failed; details withheld.") from None
    if len(receipt['verdicts']) != len(payload['tests']):
        return StepResult(False, 'invalid authenticated test count', cleanup_failed)
    if signed_failure is not None:
        return StepResult(False, signed_failure, cleanup_failed)
    if cleanup_failed:
        return StepResult(False, 'candidate left processes that could not be cleaned up', True)
    return StepResult(True, 'all tests passed')

@scorer(metrics=[accuracy()])
def verify(timeout=300):
    records = {r['problem_id']: r for r in load_records(True)}

    async def private_score(state, target):
        record = records[str(state.sample_id)]
        results = []
        halted = False
        # A trajectory stopped by a limit is scored as far as it went: its missing
        # steps fail, so the problem is INCORRECT and the run still publishes (the
        # limit itself is recorded by Inspect). Raising here made every such run an
        # unpublishable sample error (Prometheus 4.0, problem 77, 2026-09-25).
        answered = generated_steps(state.messages)
        for index, step in enumerate(record['sub_steps']):
            if index >= answered:
                results.append({'step':step['step_number'], 'passed':False, 'reason':'not generated'})
                continue
            if halted:
                results.append({'step':step['step_number'], 'passed':False, 'reason':'not run after sandbox termination'})
                continue
            code = composed_code(record, state.messages, step)
            payload = execution_request(code, step, timeout, record['required_dependencies'])
            result = await run_payload(sandbox(), payload)
            results.append({'step':step['step_number'], 'passed':result.passed, 'reason':result.reason})
            halted = result.terminal
        count = sum(r['passed'] for r in results)
        return Score(value=CORRECT if count == len(results) else INCORRECT,
                     explanation=json.dumps({'subproblems_passed':count,'subproblems_total':len(results),
                                             'subproblem_pass_fraction':count/len(results), 'steps':results}))

    async def score(state, target):
        # Raise without private traceback frames or exception context.
        try:
            return await private_score(state, target)
        except Exception:
            pass
        raise RuntimeError('Private sandbox operation failed; details withheld.') from None
    return score


async def cleanup_candidate(environment, not_before: float = 0) -> None:
    """Bounded independent UID sweep and directory deletion; stop on failure."""
    try:
        delay = not_before - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        async with asyncio.timeout(10):
            cleanup = await environment.exec(
                list(CLEANUP_COMMAND), cwd="/", timeout=5, timeout_retry=False,
            )
        if cleanup.returncode not in (0, 1):
            raise RuntimeError("UID cleanup failed")
        async with asyncio.timeout(10):
            checked = await environment.exec(
                list(QUIESCENCE_COMMAND), cwd="/", timeout=5, timeout_retry=False,
            )
        if checked.returncode != 0:
            raise RuntimeError("UID cleanup did not reach quiescence")
    except Exception:
        # The caller preserves authenticated verdicts, including on timeout.
        raise RuntimeError("Private sandbox cleanup failed; details withheld.") from None
