# Local validation

The requested command was run without an editable install:

```sh
PYTHONPATH=.:.build/test-deps PATH=/private/tmp/claude-501/-Users-jperla-josh/e031f182-dde4-417e-9aee-73c830d18854/scratchpad/bin:$PATH /Users/jperla/josh/repos/anyeval-app/.venv/bin/python -m pytest -q tests/test_proxy_execution.py -k local_dev_ground_truth
PYTHONPATH=.:.build/test-deps PATH=/private/tmp/claude-501/-Users-jperla-josh/e031f182-dde4-417e-9aee-73c830d18854/scratchpad/bin:$PATH /Users/jperla/josh/repos/anyeval-app/.venv/bin/python -m pytest -q -k 'not local_dev_ground_truth'
```

Results: **48 passed, 2 failed** dev steps and **334 passed** other tests
(combined **382 passed, 2 failed, 0 skipped**). Both custom and provider Helm
charts were rendered with the actual Helm executable. `git diff --check` passed.

The AST test splitter has been deleted. Fixtures exercise the actual supervisor
signing block and two separate subprocesses running the new candidate dispatcher
and trusted executor. Upstream tests reach the executor unchanged. Coverage
includes helper forgery in 72.1/72.2/72.3/72.5, the chained NumPy predicates in
61.5/73.9/78.2, loop-built values and shared test variables in 52.4, candidate
function references, constants, aliases, recursion, sparse component transport,
malformed/partial/oversized replies, per-test aggregate reply budgets, trusted-binding
shadowing, callable/object
rejection, executor CPU/wall failures, crashes, MemoryError and signed failures.

All **50 dev ground-truth steps** ran with the supplied read-only HDF5 symlink.
Steps **6.1, 7.1 and 47.4 now pass**. Regression coverage adds large fixture
target sets, budgets spanning multiple tests and calls, permanently rejected
channels, shared NumPy/Python random streams (including seeds and cached
Gaussian values), and malformed candidate PRNG states. The **51 proxy
regression tests pass**. No packaged assertions, targets, or step exclusions changed.

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
