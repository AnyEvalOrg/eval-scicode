"""Authenticate supervisor verdicts before inspecting status or per-test results."""
import hashlib
import hmac
import json
import re

FLAGS = ('timeout','overflow','cleanup_failed','supervisor_error','memory_exceeded','disk_exceeded')


def verify_receipt(stdout, key):
    try:
        envelope = json.loads(stdout)
        body, tag = envelope['body'], envelope['tag']
        if not hmac.compare_digest(hmac.new(key, body.encode(), hashlib.sha256).hexdigest(), tag):
            return None
        receipt = json.loads(body)
        if (type(receipt['returncode']) is not int or any(type(receipt[f]) is not bool for f in FLAGS)
                or not re.fullmatch(r'/tmp/scicode-[a-zA-Z0-9_-]+', receipt['cwd'])
                or type(receipt['verdicts']) is not list
                or any(type(v) is not bool for v in receipt['verdicts'])):
            return None
        return receipt
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):
        return None


def receipt_failure(receipt):
    for flag, reason in [('memory_exceeded','memory limit exceeded'), ('disk_exceeded','disk limit exceeded'),
                         ('cleanup_failed','candidate cleanup failed'), ('supervisor_error','candidate supervision failed'),
                         ('timeout','run timeout'), ('overflow','output limit exceeded')]:
        if receipt[flag]:
            return reason
    if receipt['returncode'] != 0:
        return 'candidate execution failed'
    if not receipt['verdicts'] or not all(receipt['verdicts']):
        return 'test comparison failed'
    return None
