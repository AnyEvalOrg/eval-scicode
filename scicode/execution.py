"""Root receives unchanged tests; the candidate receives only composed code."""
from .bindings import candidate_bindings

OUTPUT_LIMIT = 32 * 1024 * 1024


def execution_request(code, step, timeout=300, dependencies=''):
    return {'code': code, 'step_id': step['step_number'], 'tests': step['test_cases'],
            'bindings': candidate_bindings(code, step['test_cases']),
            'dependencies': dependencies, 'timeout': timeout, 'output_limit': OUTPUT_LIMIT}
