"""Unprivileged persistent candidate namespace; no tests or targets are loaded."""
import json
import os
import sys


def serve(code, channel):
    from proxy_protocol import unpack_arguments, random_state, restore_random_state
    namespace = {}
    exec(code, namespace)
    channel.send((True, random_state()))
    while True:
        operation, name, args, kwargs, state = channel.receive()
        if operation == 'stop':
            return
        try:
            value = namespace[name]
            if operation == 'call':
                restore_random_state(state)
                value = value(*unpack_arguments(args, namespace), **unpack_arguments(kwargs, namespace))
                value = (value, random_state())
            elif operation == 'bind':
                value = ('call', None) if callable(value) else ('value', value)
            else:
                raise ValueError('Invalid operation')
            channel.send((True, value))
        except BaseException:
            # No exception objects, tracebacks or candidate strings cross back.
            channel.send((False, None))


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_protocol import Channel
    with open(sys.argv[1], encoding='utf-8') as stream:
        request = json.load(stream)
    channel = Channel(int(sys.argv[2]), int(sys.argv[3]), request['timeout'])
    # Request traffic is root-authored; independently bounded per frame but not
    # by the candidate-output budget over a long sequence of legitimate calls.
    channel.remaining = 2**63
    serve(request['code'], channel)


if __name__ == '__main__':
    main()
