"""Audit every packaged test source for references flowing into candidate calls.

The packaged tests consume references only in comparisons, sometimes after
unpacking target. Conservatively propagate assignment dependencies, inspect
nested calls independently, and inspect the complete call graph of local
comparison helpers. This is a corpus compatibility check, not the runtime
provenance boundary (which lives in expected_values and Client).
"""
import ast

from scicode.dataset import load_records


def test_packaged_expected_values_are_only_passed_to_trusted_comparisons():
    exceptions = []
    steps = cases = reference_calls = 0
    for record in load_records(include_dev_set=True):
        for step in record['sub_steps']:
            steps += 1
            for index, source in enumerate(step['test_cases'], 1):
                cases += 1
                tree = ast.parse(source)
                tainted = {'target'}

                def depends(node):
                    return any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                               and n.id in tainted for n in ast.walk(node))

                while True:
                    before = set(tainted)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Assign) and depends(node.value):
                            tainted.update(n.id for dst in node.targets for n in ast.walk(dst)
                                           if isinstance(n, ast.Name))
                    if tainted == before:
                        break
                definitions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

                def trusted_helper(name, seen=()):
                    if name in seen:
                        return True
                    if name not in definitions:
                        return False
                    return all(ast.unparse(n.func).startswith('np.')
                               or trusted_helper(ast.unparse(n.func), (*seen, name))
                               for n in ast.walk(definitions[name]) if isinstance(n, ast.Call))

                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if not (any(depends(v) for v in node.args)
                            or any(depends(k.value) for k in node.keywords)):
                        continue
                    reference_calls += 1
                    name = ast.unparse(node.func)
                    if (name not in ('np.allclose', 'cmp_tuple_or_list', 'are_dicts_close')
                            and not trusted_helper(name)):
                        exceptions.append((step['step_number'], index, ast.unparse(node)))
    assert (steps, cases, reference_calls) == (341, 1082, 1041)
    assert exceptions == [], exceptions
