# SciCode for AnyEval

`eval-scicode` 1.0.0 registers **`scicode/scicode`**, an Inspect task measuring
scientific Python code generation through sequential subproblems. The published
population is **65 test problems / 291 steps**. `include_dev_set=True` appends
15 dev problems / 50 steps, preserving upstream order and exact string
`problem_id` values. Each sample gets one sequential trajectory, one epoch,
and a **CORRECT verdict only if every step passes every test**. Accuracy is the
headline main-problem pass rate. JSON explanations include the subproblem pass
fraction, counts and sanitized step statuses; there is no model judge.

## Dataset and private grading

The package contains byte-preserved, gzipped `problems_all.jsonl` and
`problems_dev.jsonl` from `scicode-bench/SciCode` revision
`69a8cfc829fe8788a426ce8b5de6292366dce7ef`. `scicode/data/manifest.json` records
source and compressed SHA-256 hashes, IDs and counts. Loading validates both
hashes and the exact population. It needs neither network access nor an upstream
checkout. Rebuild from a clone containing that commit with:

```sh
python scripts/build_dataset.py /path/to/SciCode-clone
```

`Sample.input` is exactly `problem_description_main`. Metadata, target, files
and store contain no reference code, tests or targets. The solver captures only
preformatted public prompts, including across generation failures with traceback
locals enabled. Only the scorer closes over private packaged records; private
records are not decorator arguments. The sandbox image contains only runtime helpers and the target HDF5 asset, not the
packaged dev solutions. AnyEval must never mount the source package into a
candidate sandbox. Private sandbox events and provider diagnostic logs are
suppressed using the template's pinned Inspect proxy contract, independently
of `redaction.yaml`. Errors are re-raised outside private traceback frames.

## Protocol and deliberate changes

The reference protocol is the **supplied `inspect_evals/scicode` implementation**,
not the different adapter in the original benchmark's `eval/inspect_ai` folder.
The four prompt constants are copied byte-for-byte. The system template receives
the record's dependency string. A user subproblem prompt is appended, generation
runs, and its assistant response remains in conversation for the next step.
Scientific background defaults to **False**, matching the supplied Inspect task;
set `provide_scientific_background=True` for scientist-written background.
Generation temperature/token limits are left to Inspect/model settings, as in
that adapter. Extracting code reproduces its literal removal of ` ```python `
and ` ``` ` fences and outer `.strip()`. Testing composes dependencies followed
by all preceding generated solutions through the current numbered step.
Each step gets a fresh Python process; its tests share a namespace and RNG state.
The timeout defaults to **300 seconds per step**, covering all its computations.

The source implementations disagree about known-bad steps. The original
benchmark adapter skips **13.6, 62.1 and 76.3**, carrying replacement source from
its corresponding `.txt` files forward. The supplied Inspect solver/scorer
has **no such skip or replacement branch** and evaluates those steps normally.
This package follows that supplied Inspect behavior, including generating and
carrying those responses forward. It does not silently inject reference code.
This accounts for 341 packaged steps versus the paper/README's 338. Scores
should identify this protocol/population and are not directly interchangeable
with the original adapter's 338-step results.

Other deliberate changes from supplied Inspect:

* The initial user input is the actual main-problem prompt instead of an opaque
  ID. Private record metadata and a candidate-readable HDF5 mount are removed.
* A separate root worker runs each upstream test, including
  helpers, reference calculations, wrappers, loops and assertions. Candidate
  functions are proxies: arguments and results cross private pipes using the
  bounded data-only format. The candidate code is executed once in its own
  process, so candidate functions share a namespace and call each other there.
  Candidate functions and classes are callable proxies. Instances, callables,
  iterators and other non-data results remain in a candidate-side table of at
  most 4,096 opaque handles. Only explicit attribute reads and method/function
  calls in trusted tests are forwarded. Handle equality and hashing use local
  identity; truth is false. Array/numeric conversions and other value operators
  fail locally. Library attribute probes cannot trigger RPC. Root receives only
  validated data and handle IDs, never executable objects or native pointers.
  HDF5 references and results derived from them carry trusted provenance.
  Reference arguments (including nested values, aliases, NumPy conversions and
  output buffers) fail before serialization. Trusted operations consuming
  references cannot invoke candidate callbacks.
  Argument graphs preserve object identity within each call. Post-call arrays,
  lists and dictionaries are written back into the original trusted objects,
  including nested aliases and objects detached during the call. Array shape
  or dtype changes that cannot be applied in place fail the call. Unchanged
  arrays use a lossless state marker to avoid consuming the reply budget twice.
  Subsequent calls receive the updated argument state.
  NumPy's module-level PRNG and Python's `random` state are synchronized at
  initialization and around calls using validated plain data. Test seeds,
  interleaved draws, and cached Gaussian values therefore cross the process
  boundary; independently created generator objects remain process-local.
* Pinned dependency imports and upstream `test_util` helpers live in the trusted
  executor. The original `scicode.compare.cmp` import path resolves to those
  helpers. Tests retain their tolerances, argument order and NumPy semantics.
  Binding inventory parsing runs only inside the bounded candidate process;
  parsing failures produce signed incorrect results. The trusted worker validates
  the inventory (at most 4,096 names, each at most 1,024 characters). Trusted test
  expressions are instrumented to preserve reference provenance across calls,
  operators, conversions and attribute reads; assertions are not extracted or
  split. Packaged test sources remain unchanged. Referenced module constants
  are fetched as safe data or opaque handles.
  Candidate bindings that shadow builtins, trusted imports or preloaded
  comparison helpers fail closed.
* Tests run in their original order with a shared trusted namespace. Each
  assertion executes before the next test begins. Failed calls cannot become
  passing tests even if test code catches their exceptions. Every per-test
  result is signed; a worker crash or timeout fails the entire step. Later
  subproblems still run after successful cleanup. If the pod dies or cleanup
  cannot establish quiescence, remaining steps are marked unrun/failed, so the
  denominator stays fixed. A main problem passes only when all its steps pass.
* Safe serialization admits plain types, NumPy scalars/arrays, common sparse
  matrices (data/index components, without densification) and constrained symbolic scalar representations. Arbitrary Python
  objects travel only as opaque handles; object-dtype arrays fail closed. Graphs
  are bounded by node count, nesting depth and the existing frame/traffic limits.
  Arrays use
  `np.save(allow_pickle=False)` inside bounded JSON/base64 envelopes. The worker
  checks dtype, shape and byte length before loading with `allow_pickle=False`.
  Expensive symbolic numbers, decoding failures and comparison failures cannot
  consume the signing supervisor’s execution budget.
  Candidate pickle is never deserialized. BLAS uses one thread.
* A 32 MiB aggregate result/output limit, template resource limits and the
  security isolation below bound evaluation. These limits and the pinned
  Python/numeric environment can affect unusually large or slow solutions.

## Trusted supervisor

The image is `python:3.12-slim-trixie`. NumPy **1.26.4**, SciPy **1.13.1**,
SymPy **1.13.3**, and h5py **3.11.0** are pinned. The supplied upstream Docker
requirements list those four libraries without versions. Matplotlib **3.9.2**
and pandas **2.2.3** supply additional dataset imports. SciPy stays below 1.14
because the pinned tasks import `scipy.integrate.simps`, removed in
[the 1.14 release](https://docs.scipy.org/doc/scipy-1.14.1/release/1.14.0-notes.html).

`test_data.h5` is baked in at `/opt/scicode/test_data.h5`, SHA-256 verified at
build, root-owned, mode **0400**. Its pinned Drive ID is
`17G_k65N_6yFFZ2O-jQH00Lh6iaw3z-AW`; SHA-256 is
`48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890`.
It is large, ignored by git, and excluded from distributions.

SETUP exercises the template's root/protected-memory check, cross-UID `/proc`
access, descriptor accounting, RSS visibility, writable OOM adjustment, and
writable/executable work mount. It also checks target ownership, mode and HDF5
signature, subreaper support, and the comparison worker’s limits/imports before
issuing a random 256-bit HMAC key. This task exposes no generation-time tools.
RUNNER unlinks the root-only request and launches candidate code as UID/GID
**65532**, with supplementary groups cleared and `no_new_privs` set. A separate
root executor starts with `python -I -S`, cwd `/`, and only checked root-owned,
non-writable runtime/site-package directories. It has no signing key, candidate
code, or candidate output descriptors. Its only extra descriptors are the two
RPC pipe endpoints; the candidate cannot access the executor's verdict stream.
Targets are opened by that executor after exec.

The executor has hard **768 MiB RLIMIT_AS**, **300 seconds RLIMIT_CPU**,
**64 RLIMIT_NOFILE**, no core dumps, and a supervisor-enforced wall deadline
covering the whole step (300 seconds by default). A seccomp filter denies socket
and network syscalls in addition to container/pod network isolation. JSON,
NumPy/SymPy decoding and all test predicates happen in that limited worker.
The signing supervisor consumes only fixed verdict bytes authored by the worker.
Executor crashes, timeouts, MemoryError and late prerequisite failures yield a
**signed INCORRECT**, preserving time to sign within the outer deadline.

The inherited template bounds NPROC at 64, NOFILE at 256, AS/DATA at 5 GiB,
CORE at zero, and file size at the output limit. A 50 ms watchdog bounds
aggregate candidate RSS at 4 GiB. A 100 ms disk watchdog bounds `/tmp`,
`/var/tmp`, `/dev/shm` and descriptor-retained files/memfds at 256 MiB / 10,000
entries, deduplicated by inode. Root has SYS_PTRACE for cross-UID descriptor
accounting; the irreversible child credential drop clears capabilities.
The 6 GiB pod budget allocates 4096 MiB to candidate RSS, up to 768 MiB to
the executor address space, and 256 MiB to watched files, leaving 512 MiB
for the supervisor, runtime overhead and sampling bursts. Per-process 5 GiB
AS/DATA limits allow virtual mappings above the aggregate resident-memory cap.
Sampling cannot enumerate all kernel memory allocations; pod attribution is
the backstop. Pipe replies have bounded length-prefixed frames and a 32 MiB
aggregate receive budget per trusted test (and separately for initialization
and binding setup). Only the trusted test loop resets that budget; the step's
wall/CPU deadlines remain unchanged. A rejected frame permanently invalidates
the channel. Malformed, oversized and unsupported results fail
closed; result files are no longer used.
Receipts contain only statuses, per-test booleans, a working-directory ID and,
when requested by the canonical check, a numeric peak candidate RSS in bytes.
No code, candidate streams, targets or answer bytes enter explanations/logs.

An authenticated failure is **INCORRECT**. After successful SETUP, missing or
invalid authentication is a withheld-details **RuntimeError**, except for
same-pod-UID kernel evidence of OOM/storage exhaustion under the template's
unchanged `sandbox_state.py`. This includes OOMKilled / container exit 137 or
signal 9, and Failed/Evicted pods explicitly naming ephemeral-storage. SETUP
failure, Spot preemption, transport failures and inconclusive pod state are
harness errors, not incorrect answers. Independent bounded cleanup runs on
every path, including cancellation. Cleanup cannot erase signed or
kernel-attributed failures. A valid success followed by failed cleanup is
incorrect. The per-sample pod is then discarded.

The Helm chart uses gVisor, Spot nodes, no service-account token, root read-only
filesystem, disk-backed `/tmp`, read-only `/dev/shm`, requests equal to limits
(1 CPU / 6 GiB RAM / 1 GiB ephemeral storage), and a release-scoped deny-all
Ingress/Egress NetworkPolicy. Compose mirrors capabilities, isolation and
memory/PID limits; its anonymous `/tmp` volume has no portable disk quota.
The template watchdog remains active. `anyeval_chart=False` explicitly opts
into the provider chart; the custom chart is the default.

## Build and run

Fetch the asset locally, then build/push the operator image:

```sh
python -m pip install gdown==5.2.0
python scripts/fetch_test_data.py
gcloud builds submit --config scripts/cloudbuild-image.yaml .
```

If gcloud excludes ignored files, use the included `.gcloudignore` (which
explicitly includes `scicode/test_data.h5`). The build independently verifies
the hash and permissions. It publishes
`us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-scicode-sandbox:1.0.0`.
The chart references that exact tag; operators should resolve a digest for
published-run provenance. Local build: `docker build -f scicode/Dockerfile -t
eval-scicode-sandbox:local .`. Compose uses the prebuilt production image; set
`SCICODE_SANDBOX_IMAGE=eval-scicode-sandbox:local` to use your local build. It
does not try to rebuild from an installed wheel, which intentionally omits HDF5.

Install a **noneditable** package for registration:

```sh
python -m pip install '.[anyeval]'
inspect eval scicode/scicode --model PROVIDER/MODEL
inspect eval scicode/scicode --model PROVIDER/MODEL -T sandbox_type=docker
inspect eval scicode/scicode --model PROVIDER/MODEL -T provide_scientific_background=true
```

`[inspect]` pins Inspect 0.3.260; `[anyeval]` also pins k8s-sandbox 0.13.0.
`run.py --model PROVIDER/MODEL --limit 1` is a source-tree entry point.
Do not use editable installs in an AnyEval registry environment.

## Canonical and regression checks

Every dev step has `ground_truth_code`; the test records do not provide
per-step ground truth. `scripts/canonical_check.py` composes the dev solutions,
runs the **real SETUP/RUNNER**, verifies HMAC receipts, and runs all **50 dev
steps**. A step is reported as `known_upstream_defect` only when the authenticated
per-case verdicts fail exactly the documented **one-based case indices**:
**78.3 cases 1, 2, 3**, or **70.8 case 4**, with every other case passing.
Any other comparison failure pattern is unexpected, including an extra failing
case, a missing documented failure, or all four cases of 70.8 failing.
Known defects remain failed comparisons, not passes. It exits
0 when every other step passes (normally **48 passed, 2 known upstream defects**),
1 for unexpected failures, and 2 for infrastructure/check errors. Memory,
timeout, cleanup and other failures are unexpected even on those two steps.
It emits only step IDs, statuses, failing case indices (`failed_cases`), counts
and image/revision provenance, plus optional numeric RSS measurements; it does
not modify the task population.
Run in a disposable Linux container through:

```sh
gcloud builds submit --config scripts/cloudbuild-canonical.yaml .
```

Cloud Build launches the image with the same UID-dropping capabilities,
read-only root, no network, 6 GiB budget and PID limit as Compose. The source
mount is for this operator-only reference check, never for production
candidates. The config enables `--peak-rss`: each result and the summary include
`peak_candidate_rss_bytes`, the maximum aggregate candidate RSS observed by the
50 ms watchdog (bytes, including descendants, excluding the root executor).
This sampled maximum can miss peaks between polls. Without the flag, the field
is omitted. The summary is the maximum across all 50 steps. No candidate data
is included. The operator’s original 2 GiB run reported **47/50 passed**:
10.11 hit the old 768 MiB candidate cap, and the two known defects failed
comparison. Rerun with the new budget to measure 10.11 and the dev-wide maximum.
There is no complete test-set canonical check because that ground truth is
not shipped upstream.

Install test dependencies noneditably, or stage them in `.build/test-deps`
with `pip install --target .build/test-deps ...` when the caller's environment
must remain untouched. The suite performs no network or model calls and does
not run arbitrary candidates on the developer host. Its supervisor protocol
fixtures use authored child programs and stub Linux credential/prerequisite
operations, including RLIMIT_AS on macOS; they do not claim to prove kernel
containment. Authored comparison workers exercise real process separation,
CPU/wall deadlines, crashes, MemoryError and signed failure handling. The actual
Linux supervisor path is used by the canonical check.

```sh
PYTHONPATH=. python -m pytest -q
```

Tests cover all packaged IDs and unchanged test source, byte-equal upstream
prompts, comparisons against retained upstream helpers, safe serialization,
actual signing/comparison code with authored fixtures, receipt forgery,
watchdogs, SETUP prerequisites, missing-receipt/kernel attribution, generation
traceback privacy, test-helper forgery, chained NumPy predicates, loop-built
values, bounded executor failures, private publication, and real Helm rendering.
All 50 dev steps are enumerated for local reference checks; when the HDF5 asset
is absent, steps needing it are explicitly skipped. The operator canonical check requires
all 50 steps and uses the same proxy executor. Helm must be on PATH.

With the read-only `scicode/test_data.h5` symlink available, the 2026-09-22 host
dev check reports **48 passed, 2 failed, 0 skipped**:

```sh
PYTHONPATH=.:.build/test-deps python -m pytest -q tests/test_proxy_execution.py -k local_dev_ground_truth
```

The proxy repairs address general transport and execution behavior:

* **6.1 / 7.1:** the host harness originally put targets in argv (6.1:
  `E2BIG`, about 28 MB) and encoded the entire target set as one frame (7.1:
  above 32 MiB). Fixture targets now load from individual temporary files.
  After those harness repairs, trusted-executor tracing showed `Reply budget
  exceeded` in 6.1 case 4 and 7.1 case 3 (with subsequent calls also failing).
  Each large reply was 13,653,744 bytes; even the two-call final cases fit
  within 32 MiB, but accumulated replies from earlier tests did not. The
  per-test budget fixes both steps without removing frame or traffic limits.
  Mutation replies represent unchanged input arrays by reference to their
  original state, keeping the two-call cases within that same budget.
* **47.4:** all three assertions failed because `np.random.seed(1024)` ran
  only in the trusted test process. Synchronizing random state in both
  directions preserves the Monte Carlo stream across calls and later tests.
  Argument mutation is not the cause of these assertions: each case builds
  fresh positions and compares the returned energy trace.

The following are **known upstream dev-set defects**. The operator confirmed
that reference code fails its own stored targets in the pinned
`eval-scicode-sandbox:1.0.0` image, with `test comparison failed` for both steps.
They also reproduce with unchanged tests in one host Python process using
`process_hdf5_to_tuple`; these are dataset defects, not host-version issues.
Dev problems are not published, so no exclusion from the published **65 main
problems** is needed. All 50 dev steps still execute with their original assertions:

| Step | Failing cases (one-based) | Host diagnosis |
| --- | --- | --- |
| 78.3 | 1–3 | The timing-weighted error metric selects `dt=0.001`, returning shapes `(10001, 2)`, `(20001, 2)`, `(15001, 2)`. Targets have shapes `(2, 2)`, `(3, 2)`, `(2, 2)` and match trajectories at `dt=10`. `np.allclose` raises a broadcasting `ValueError`. |
| 70.8 | 4 (1–3 pass) | `AssertionError`; maximum probability error about `1.7914e-4`. At `L=1.611792e22`, computed phases are of order `1e10`. A relative Hamiltonian perturbation of `1e-15` changes a probability by about `1.2502e-4`, demonstrating sensitivity to floating-point rounding. |

## Historical published baselines and licensing

The supplied original clone's README (revision
`e3158ea011d4235245a547460d3688d7ccbf9900`, retained as `UPSTREAM-README.md`)
labels its leaderboard columns **“Main Problem Resolve Rate”** and
**“Subproblem”**. These values are transcribed exactly; they are historical
published results, not measurements of this package or its changed isolation:

| Model (upstream label) | Main problem (%) | Subproblem (%) |
|---|---:|---:|
| OpenAI o3-mini-low | 10.8 | 33.3 |
| OpenAI o3-mini-high | 9.2 | 34.4 |
| OpenAI o3-mini-medium | 9.2 | 33.0 |
| OpenAI o1-preview | 7.7 | 28.5 |
| Deepseek-R1 | 4.6 | 28.5 |
| Claude3.5-Sonnet | 4.6 | 26.0 |
| GPT-4o | 1.5 | 25.0 |
| GPT-4-Turbo | 1.5 | 22.9 |

The README does not fully specify each leaderboard run's configuration; no
additional background-mode or sampling claims are inferred. See the protocol
population distinction above before making comparisons. The associated paper
is **SciCode: A Research Coding Benchmark Curated by Scientists**, Tian et al.,
[arXiv:2407.13168](https://arxiv.org/abs/2407.13168).

The original clone's LICENSE was verified as **Apache-2.0** and is retained.
SciCode records, attributed upstream adaptations and this AnyEval package are
redistributed under Apache-2.0. `NOTICE.md` records original authors and adapter
changes. The obsolete OpenEvalz stub and its unrelated content are replaced.
