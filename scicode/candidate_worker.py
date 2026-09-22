"""Unprivileged persistent candidate namespace; no tests or targets are loaded."""
import json
import os
import sys


def serve(code, channel):
    import operator
    import numpy as np
    from bindings import candidate_bindings
    from proxy_protocol import random_state, restore_random_state
    from rpc_objects import Handles, GraphEncoder, decode_graph, array_digest
    # Both parsing and execution are contained by the candidate's limits.
    bindings = candidate_bindings(code)
    namespace = {}
    exec(code, namespace)
    channel.send((True, random_state(), bindings))
    handles = Handles()

    def remote(kind, data):
        return namespace[data] if kind == 'binding' else handles.get(data)

    while True:
        operation, target, graph, state = channel.receive()
        try:
            (args, kwargs), mutable = decode_graph(graph, remote)
            unchanged = {id(v): (i, array_digest(v)) for i, v in enumerate(mutable)
                         if type(v) is np.ndarray}
            restore_random_state(state)
            value = remote(*target)
            status = 'ok'
            try:
                if operation == 'call':
                    value = value(*args, **kwargs)
                elif operation == 'bind':
                    pass
                elif operation == 'getattr':
                    value = getattr(value, *args)
                elif operation == 'setattr':
                    value = setattr(value, *args)
                elif operation == 'method':
                    name, *operands = args
                    # Use Python's protocols (including reflected/in-place
                    # fallbacks), rather than requiring every dunder to exist.
                    unary = {'__iter__': iter, '__next__': next, '__len__': len,
                             '__bool__': bool, '__int__': int, '__float__': float,
                             '__complex__': complex, '__round__': round}
                    if name == '__len__' and not hasattr(type(value), '__len__'):
                        status, value = 'no_length', None
                    elif name in unary:
                        value = unary[name](value, *operands, **kwargs)
                    elif hasattr(operator, name):
                        value = getattr(operator, name)(value, *operands, **kwargs)
                    elif name.startswith('__r') and hasattr(operator, '__' + name[3:]):
                        value = getattr(operator, '__' + name[3:])(operands[0], value, *operands[1:])
                    else:
                        value = getattr(value, name)(*operands, **kwargs)
                else:
                    raise ValueError('Invalid operation')
            except StopIteration as exc:
                status, value = 'stop', exc.value
            except AttributeError:
                status, value = ('missing' if operation == 'getattr' else 'error'), None
            except BaseException:
                status, value = 'error', None
            encoder = GraphEncoder(lambda v: ('handle', handles.put(v)), unchanged)
            updates = [encoder.add(v) for v in mutable]
            channel.send({'status': status, 'graph': encoder.graph(value),
                          'updates': updates, 'state': random_state()})
        except BaseException:
            # No exception objects, tracebacks or candidate strings cross back.
            channel.send(None)


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
