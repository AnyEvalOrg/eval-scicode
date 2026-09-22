# Local validation

The requested command was run without an editable install:

```sh
PYTHONPATH=. PATH=/private/tmp/claude-501/-Users-jperla-josh/e031f182-dde4-417e-9aee-73c830d18854/scratchpad/bin:$PATH /Users/jperla/josh/repos/anyeval-app/.venv/bin/python -m pytest -q
```

Result: **285 passed, 0 failed, 0 skipped**. Both custom and provider Helm charts
were rendered with the actual Helm executable. `git diff --check` passed.

The caller's environment has NumPy 2.5.2 and lacked SciPy/SymPy/h5py. Network
installation was unavailable. SciPy 1.17.1 and SymPy 1.14.0 (plus mpmath) were
copied from the existing local uv cache into ignored `.build/test-deps` solely
for host comparison/serialization fixtures. The caller's environment and
registry were not modified. These fixture results do **not** validate the
image's pinned numeric versions or real HDF5 loading.

A noneditable wheel was built without dependencies/build isolation, installed
only into ignored `.build/wheel-env/site-packages`, and checked by
`scripts/verify_wheel.py`. Cold Inspect entry-point discovery succeeded with
network disabled and checkout imports removed: `scicode/scicode`, 65 test
samples, 80 with dev, and packaged Docker/Helm/data assets present.

The large `test_data.h5` asset was not downloaded, and the Docker image, cloud
build/push, real gVisor containment and full dev canonical run were not executed
locally. The provided build and canonical Cloud Build commands are operator
steps. The canonical script requires all 50 dev steps to pass and does not
silently exclude failures. Host supervisor fixtures exercise real signing and
comparison code with authored child programs and mocked credential operations;
they are not a substitute for that image-backed check.

No commit was created.
