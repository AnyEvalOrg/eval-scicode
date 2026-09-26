"""Root receives unchanged tests; the candidate receives only composed code."""

OUTPUT_LIMIT = 32 * 1024 * 1024
# Aggregate candidate replies per trusted test. Measured largest: 170,786,610 bytes
# (63.2 case 2); 256 MiB leaves 1.57x headroom. OUTPUT_LIMIT still bounds candidate
# stdout/stderr and file sizes.
REPLY_LIMIT = 256 * 1024 * 1024


def execution_request(code, step, timeout=300, dependencies='', targets_sha256=None):
    request = {'code': code, 'step_id': step['step_number'], 'tests': step['test_cases'],
               'dependencies': dependencies, 'timeout': timeout, 'output_limit': OUTPUT_LIMIT,
               'reply_limit': REPLY_LIMIT}
    if targets_sha256 is not None:
        # SETUP compares this with the image's root-owned target marker, so a task
        # can never grade against another variant's HDF5 targets.
        request['targets_sha256'] = targets_sha256
    return request
