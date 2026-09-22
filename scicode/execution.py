"""Only root receives test definitions; the child gets target-free computations."""
from .test_plan import split_test

OUTPUT_LIMIT = 32 * 1024 * 1024


def execution_request(code, step, timeout=300):
    # Validate all source plans before issuing SETUP. Never send ground truth.
    for test in step['test_cases']:
        split_test(test)
    return {'code':code, 'step_id':step['step_number'], 'tests':step['test_cases'],
            'timeout':timeout, 'output_limit':OUTPUT_LIMIT}
