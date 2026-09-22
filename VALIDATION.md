# Local validation

The requested command was run without an editable install:

```sh
PYTHONPATH=.:.build/test-deps PATH=/private/tmp/claude-501/-Users-jperla-josh/e031f182-dde4-417e-9aee-73c830d18854/scratchpad/bin:$PATH /Users/jperla/josh/repos/anyeval-app/.venv/bin/python -m pytest -q
```

Results: **48 passed, 2 failed** dev steps. The full requested command reports
**461 passed, 2 failed, 0 skipped** in 179.70 seconds (including **413 passing non-dev tests**). Both custom and provider Helm
charts were rendered with the actual Helm executable. `git diff --check` passed.

Fixtures exercise the actual supervisor signing block and two separate
subprocesses running the candidate dispatcher and trusted executor. Packaged
sources are unchanged. Trusted expressions are instrumented to carry reference
provenance through calls, NumPy operations (including asarray/copy/reductions),
operators, attribute reads, indexing and scalar conversions. Assertions are not
extracted or split. Coverage
includes helper forgery in 72.1/72.2/72.3/72.5, the chained NumPy predicates in
61.5/73.9/78.2, loop-built values and shared test variables in 52.4, candidate
function references, constants, aliases, recursion, sparse component transport,
malformed/partial/oversized replies, per-test aggregate reply budgets, trusted-binding
shadowing, opaque callable/object handles, executor CPU/wall failures, crashes, MemoryError and signed failures.

All **50 dev ground-truth steps** ran with the supplied read-only HDF5 symlink.
Steps **6.1, 7.1 and 47.4 now pass**. Regression coverage adds large fixture
target sets, budgets spanning multiple tests and calls, permanently rejected
channels, shared NumPy/Python random streams (including seeds and cached
Gaussian values), and malformed candidate PRNG states. The existing **51 proxy regression tests pass**. No packaged assertions, targets, or step exclusions changed.

Candidate binding inventory now runs inside the resource-limited candidate
worker, including in the image-backed canonical script. The valid 1,001-term
addition source raises the reproduced inventory RecursionError there and
produces a signed incorrect receipt. A scorer regression exercises real worker
subprocesses behind the sandbox adapter and verifies SETUP, RUNNER and cleanup
calls, with an INCORRECT score instead of a harness exception.

Object-protocol regressions cover opaque classes, instances and closures;
explicit attributes, methods and calls; handle limits; malformed graphs;
rejection of implicit value protocols and native NumPy pointer interfaces;
shared argument identities; nested/cyclic lists and dictionaries; detached
mutable arguments; array/list/dict updates in place across calls; and rejected
array shape/dtype changes. Unchanged arrays use a lossless state marker, keeping
6.1 and 7.1 within the existing 32 MiB per-test traffic budget.

Steps **30.1, 46.1 and 68.1** run their unchanged upstream tests against the real
HDF5 targets using an authored analytic Slater implementation. **13.12** runs
its unchanged tests with an authored finite-difference Maxwell/ICN reference;
its fixture targets come from direct execution of that reference, with an
independent central-divergence calculation. These four steps are in the test
split and have no locally packaged ground truth. The Maxwell fixture verifies
object transport and retained remote state, not agreement with the unpublished
upstream implementation. All **17 object-protocol tests pass**.

The candidate worker no longer exposes an operator-dispatch RPC. Handle equality
and hashing use local identity, truth is false, and array/numeric conversions
and arithmetic fail locally. Explicit test attribute reads use the trusted
adapter; implicit library probes cannot issue RPC. Reference wrappers are
rejected before serialization, and trusted operations consuming references
cannot call back into the candidate, including through lazy iterators. Mutable
output buffers and aliases remain protected across test cases.

New regressions cover dishonest equality, truth, conversions and arithmetic;
expected scalars, strings, arrays, containers and sparse matrices; nested
arguments, derived values, out buffers, aliases, callbacks and caught failures.
A candidate-side receive hook verifies that rejected arguments produce **zero
request frames**; an independent subsequent probe verifies a live channel.
All **78 reference/object/audit tests pass**, including the four required object
fixtures and the output-buffer view/cached-method alias regressions.

The source audit examines **all 1,082 test_cases sources across 341 packaged
steps**, propagates target aliases/unpacking, checks nested calls independently,
and inspects local comparison helpers. It found **no exceptions**: expected
values flow only into trusted comparisons, never candidate calls. The audit is
retained as `tests/test_reference_audit.py`; it is a corpus compatibility check,
separate from the runtime reference guard.

**78.3 and 70.8 remain failures**, also reproduced with unchanged tests and
ground truth in one plain Python process. 78.3 cases 1–3 select fine-timestep
trajectories whose shapes differ from the coarse-timestep targets. 70.8 case 4
amplifies tiny rounding changes through phases of order 1e10. See [README.md](README.md)
for case-level evidence and the required image recheck. These observations
establish host failures, not a confirmed NumPy/SciPy version regression.

The host environment has NumPy 2.5.2. SciPy 1.17.1 and SymPy 1.14.0 (plus mpmath)
are staged in ignored `.build/test-deps` from the existing local cache; h5py
3.16.0 is also available there. The caller's environment and registry were not
modified. These results do not
validate the image's pinned numeric versions or protected image HDF5 loading.
The host did load real targets via `process_hdf5_to_tuple`. An attempt to
install NumPy 1.26.4 / SciPy 1.13.1 into a separate ignored directory for a
version comparison failed because DNS was unavailable.

Linux UID changes, `/proc` containment, protected import directories, seccomp
and address-space limits are stubbed in the host subprocess fixtures; CPU and
wall deadlines and pipe serialization are real. macOS may return EPERM for an
already-exited process group, so the host harness uses owned Popen handles
for its authored fixtures, which never fork.
Production retains process-group and independent UID cleanup. No arbitrary
external candidate code was run on the host.

The supplied HDF5 symlink was read without modifying or committing the asset.
The Docker image, cloud build/push, real gVisor containment and full dev
canonical run were not executed locally.
`scripts/canonical_check.py` uses the new proxy path, passes the pinned dependency
source separately, and requires all 50 dev steps to pass. It remains the
operator's image-backed verification step.

No commit was created.
