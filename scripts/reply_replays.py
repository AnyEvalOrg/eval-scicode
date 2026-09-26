"""Exact-target replays of the largest-reply SciCode test steps (both populations).

No test-set reference solutions exist. Each replay is an authored candidate whose
functions return the stored target itself (widened to 64-bit dtypes, the worst case for
a numerically equal answer) with the call pattern of a straightforward correct solution:
intermediate calls return arrays of the true shapes, and forward_iteration mutates its
argument in place and returns it. Unchanged tests then compare that value with the target,
so a correct answer of this size must pass. These are harness checks, not solutions.

Used by tests/test_reply_budget.py (host, two real processes) and by
scripts/canonical_check.py --replays (image, real SETUP/RUNNER and limits).
Stdlib-only at import time so the image's runtime Python can load it.
"""

# Steps whose stored targets (or call patterns) produce the largest candidate replies:
# every test case whose widened target encoding exceeds 32 MiB, plus all other cases of
# those steps. Largest measured traffic: 63.2 case 2 (one 162.8 MiB frame) and 63.4
# case 2 (three 61.0 MiB frames in one test); see README.md.
STEPS = ('13.14', '53.4', '63.2', '63.3', '63.4', '63.5')

_PRELUDE = '''
import pickle as _pickle
import numpy as _np
import scipy.sparse as _sparse
with open(REPLAY_PATH, "rb") as _stream:
    _T = _pickle.load(_stream)
_I = [0]
def _next():
    _I[0] += 1
    return _T[_I[0] - 1]
def _widen(value):
    if isinstance(value, _np.ndarray):
        kind = value.dtype.kind
        if kind in "biu" and value.dtype != bool:
            return value.astype(_np.int64)
        if kind == "f":
            return value.astype(_np.float64)
        if kind == "c":
            return value.astype(_np.complex128)
        return value
    if isinstance(value, (tuple, list)):
        return type(value)(_widen(v) for v in value)
    return value
'''

_GRID = '''
def initialize_grid(price_step, time_step, strike, min_price, max_price):
    return (_np.linspace(min_price, max_price, price_step), 1.0,
            _np.linspace(0.0, 1.0, time_step), 1.0)
'''

SOURCES = {
    '13.14': '''
class Maxwell:
    def __init__(self, n_grid, x_out):
        self.n_grid, self.x_out = n_grid, x_out
def initialize(maxwell):
    for name, value in zip(("E_x", "E_y", "E_z", "A_x", "A_y", "A_z", "phi"), _widen(_next())):
        setattr(maxwell, name, value)
    return maxwell
''',
    '53.4': '''
def predator_prey(prey, predator, alpha, beta, gamma, T):
    return tuple(_widen(_next()))
''',
    '63.2': _GRID + '''
def apply_boundary_conditions(N_p, N_t, p, T, strike, r, sig):
    return _widen(_next())
''',
    '63.3': '''
def construct_matrix(N_p, dp, dt, r, sig):
    return _sparse.csr_matrix(_widen(_next()))
''',
    '63.4': _GRID + '''
def apply_boundary_conditions(N_p, N_t, p, T, strike, r, sig):
    return _np.zeros((N_p, N_t))
def construct_matrix(N_p, dp, dt, r, sig):
    return _sparse.diags([1.0, -2.0, 1.0], [-1, 0, 1], shape=(N_p - 2, N_p - 2), format="csr")
def forward_iteration(V, D, N_p, N_t, r, sig, dp, dt):
    V[...] = _widen(_next())
    return V
''',
    '63.5': '''
def price_option(price_step, time_step, strike, r, sig, max_price, min_price):
    return _widen(_next())
''',
}


def candidate_code(dependencies, step_id, replay_path):
    """Composed candidate program for one replay; replay_path holds pickle.dumps(targets)."""
    return '\n'.join([dependencies, f'REPLAY_PATH = {str(replay_path)!r}', _PRELUDE, SOURCES[step_id]])
