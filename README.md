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
* A separate root worker decodes candidate operands and executes the upstream
  assertions under address-space, CPU and wall-time limits.
  Candidate computation cannot monkeypatch root comparisons or read targets.
  Test ASTs are split from the pinned source, including target unpacking,
  compound assertions, tolerances, and the dev `are_equivalent` helper. Nested
  predicates, reductions, arithmetic, comprehensions and named predicates return
  underlying numerical outputs; their boolean results are computed in the worker.
  Six dev `test_case_N` wrappers are flattened. Constant input-derived references are
  recomputed in the worker. No candidate Python is evaluated by root.
* Comparisons preserve upstream `cmp_tuple_or_list`, `are_dicts_close`,
  `are_csc_matrix_close` and NumPy allclose semantics, including argument order,
  default `rtol=1e-5`, `atol=1e-8`, `equal_nan=False`, explicit test tolerances,
  symbol-key handling and upstream exception behavior. The upstream comparison
  and HDF5 reader modules are copied unchanged. The two hardcoded benchmark
  comparison imports are removed during plan compilation, matching Inspect's
  intended cleanup without mutating the packaged source.
* Assertions happen after the child finishes, so computations are not stopped
  at the first failed comparison. Every per-test result is signed. A runtime
  failure prevents the step from passing; later subproblems still run after
  successful cleanup. If the pod dies or cleanup cannot establish quiescence,
  remaining steps are marked unrun/failed, so the denominator stays fixed.
* Safe serialization admits plain types, NumPy scalars/arrays, common sparse
  matrices and constrained symbolic scalar representations. Arbitrary Python
  objects and object-dtype arrays fail closed. Arrays use
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
RUNNER unlinks the root-only request, stages a protected driver, and launches only candidate computation as
UID/GID **65532**, with supplementary groups cleared and `no_new_privs` set.
The root process is nondumpable; all child descriptors are closed before exec.
Target values are loaded by the comparison worker only after the child and
descendants have stopped. The worker has hard **768 MiB RLIMIT_AS**, **8 seconds
RLIMIT_CPU**, and a supervisor-enforced **10-second wall deadline** for all tests
in a step. It starts via exec with no signing key and closes inherited
descriptors except its redirected standard streams. All candidate result file reads, JSON/NumPy/SymPy decoding and
numerical comparisons happen there. The supervisor consumes only fixed verdict
bytes authored by the worker. Worker crashes, timeouts, MemoryError and late
prerequisite failures yield a **signed INCORRECT**, preserving time to sign
within the outer deadline. These budgets also apply to target loading and can
affect unusually expensive comparisons.

The inherited template bounds NPROC at 64, NOFILE at 256, AS/DATA at 1 GiB,
CORE at zero, and file size at the output limit. A 50 ms watchdog bounds
aggregate candidate RSS at 768 MiB. A 100 ms disk watchdog bounds `/tmp`,
`/var/tmp`, `/dev/shm` and descriptor-retained files/memfds at 256 MiB / 10,000
entries, deduplicated by inode. Root has SYS_PTRACE for cross-UID descriptor
accounting; the irreversible child credential drop clears capabilities.
Sampling cannot enumerate all kernel memory allocations; pod attribution is
the backstop. Output reads occur after UID sweeps, are nonblocking, bounded,
no-follow, and reject symlinks, hardlinks, nonregular files and wrong ownership.
Receipts contain only statuses, per-test booleans and a working-directory ID.
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
(1 CPU / 2 GiB RAM / 1 GiB ephemeral storage), and a release-scoped deny-all
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
runs the **real SETUP/RUNNER**, verifies HMAC receipts, and expects **50/50 dev
steps** to pass. It exits nonzero on any failure, without creating exclusions
or modifying the task population. It emits only step IDs, statuses, counts and
image/revision provenance. Infrastructure failures exit separately. Run in a
disposable Linux container through:

```sh
gcloud builds submit --config scripts/cloudbuild-canonical.yaml .
```

Cloud Build launches the image with the same UID-dropping capabilities,
read-only root, no network, 2 GiB budget and PID limit as Compose. The source
mount is for this operator-only reference check, never for production
candidates. No claim of a successful full canonical run is bundled here: the
large HDF5 asset/image must be built and this command run by the operator.
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

Tests cover all packaged IDs and target-free samples/plans, byte-equal upstream
prompts, comparisons against retained upstream helpers, safe serialization,
actual signing/comparison code with authored fixtures, receipt forgery,
watchdogs, SETUP prerequisites, missing-receipt/kernel attribution, generation
traceback privacy, nested numerical predicates, bounded comparison-worker
failures, private publication, and real Helm rendering. Helm must be on PATH.

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
