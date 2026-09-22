"""Split trusted upstream assertions into unprivileged operands and root comparisons.

Only pinned dataset source enters this compiler. Candidate bytes never enter a
root eval/exec; the root expression is built exclusively from trusted test ASTs.
"""
import ast
import copy

COMPARATORS = {'np.allclose','np.isclose','np.isnan','cmp_tuple_or_list','are_dicts_close','are_csc_matrix_close','are_equivalent'}


def split_test(source):
    tree = ast.parse(source.replace('from scicode.compare.cmp import cmp_tuple_or_list','').replace('from scicode.compare.cmp import are_dicts_close',''))
    # Six dev tests wrap a single assertion in a zero-argument test_case_N.
    # Flatten those trusted wrappers so their operands cross the same boundary.
    wrapped = False
    if (len(tree.body) == 2 and isinstance(tree.body[0], ast.FunctionDef)
            and tree.body[0].name.startswith('test_case_')
            and isinstance(tree.body[1], ast.Expr)
            and isinstance(tree.body[1].value, ast.Call)
            and ast.unparse(tree.body[1].value.func) == tree.body[0].name
            and not tree.body[0].args.args):
        tree.body = tree.body[0].body
        wrapped = True
    aliases = {'target'}
    constants = {'np'}
    constant_setup = []
    predicates = {}
    root_setup, child, operands, assertions = [], [], [], []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n,ast.Name) and n.id in aliases for n in ast.walk(node.value)):
            if not isinstance(node.value, ast.Name) or node.value.id != 'target':
                raise ValueError('Unsupported target dependency')
            root_setup.append(node)
            aliases.update(n.id for t in node.targets for n in ast.walk(t) if isinstance(n,ast.Name))
        elif isinstance(node,ast.Assert):
            assertions.append(node.test)
        else:
            if any(isinstance(n,ast.Name) and n.id in aliases for n in ast.walk(node)):
                raise ValueError('Target in candidate computation')
            child.append(node)
            # Recompute trusted input-derived reference values in root, rather
            # than trusting a candidate-controlled 'expected_output' variable.
            if isinstance(node, ast.Assign):
                names = {n.id for n in ast.walk(node.value) if isinstance(n,ast.Name)}
                assigned = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n,ast.Name)}
                if names <= constants and 'random' not in ast.unparse(node.value):
                    constant_setup.append(node)
                    constants.update(assigned)
                else:
                    constants.difference_update(assigned)
                # Some pinned tests name their predicates before asserting a
                # tuple of them (80.7). Keep their numerical calculations on
                # the same trusted side as predicates written inside assert.
                for name in assigned:
                    predicates.pop(name, None)
                if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and any(isinstance(n, (ast.Compare, ast.BoolOp))
                                or isinstance(n, ast.Call) and ast.unparse(n.func) in COMPARATORS
                                for n in ast.walk(node.value))):
                    predicates[node.targets[0].id] = node.value
    if not assertions:
        raise ValueError('Missing assertion')

    def target_only(node):
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        return bool(names & aliases) and names <= aliases

    local_names = set()

    def operand(node):
        if isinstance(node, ast.Name) and node.id in predicates:
            return operand(predicates[node.id])
        if (isinstance(node, ast.Constant) or target_only(node)
                or {n.id for n in ast.walk(node) if isinstance(n,ast.Name)} <= constants | local_names):
            return copy.deepcopy(node)
        return transform(node)

    def capture(node):
        index = len(operands)
        operands.append(copy.deepcopy(node))
        return ast.Subscript(value=ast.Name(id='values',ctx=ast.Load()), slice=ast.Constant(index),ctx=ast.Load())

    def transform(node):
        node = copy.deepcopy(node)
        if isinstance(node,ast.BoolOp):
            node.values = [operand(v) for v in node.values]
        elif isinstance(node,ast.Compare):
            node.left = operand(node.left)
            node.comparators = [operand(v) for v in node.comparators]
        elif isinstance(node,ast.UnaryOp):
            node.operand = operand(node.operand)
        elif isinstance(node,ast.BinOp):
            node.left, node.right = operand(node.left), operand(node.right)
        elif isinstance(node,(ast.Tuple,ast.List,ast.Set)):
            node.elts = [operand(v) for v in node.elts]
        elif isinstance(node,ast.Dict):
            node.keys = [operand(v) if v is not None else None for v in node.keys]
            node.values = [operand(v) for v in node.values]
        elif isinstance(node,ast.IfExp):
            node.test, node.body, node.orelse = operand(node.test), operand(node.body), operand(node.orelse)
        elif isinstance(node,(ast.GeneratorExp,ast.ListComp,ast.SetComp,ast.DictComp)):
            previous = local_names.copy()
            try:
                for generator in node.generators:
                    generator.iter = operand(generator.iter)
                    local_names.update(n.id for n in ast.walk(generator.target) if isinstance(n,ast.Name))
                    generator.ifs = [operand(v) for v in generator.ifs]
                if isinstance(node,ast.DictComp):
                    node.key, node.value = operand(node.key), operand(node.value)
                else:
                    node.elt = operand(node.elt)
            finally:
                local_names.clear()
                local_names.update(previous)
        elif isinstance(node,ast.Subscript):
            node.value, node.slice = operand(node.value), operand(node.slice)
        elif isinstance(node,ast.Slice):
            node.lower = operand(node.lower) if node.lower is not None else None
            node.upper = operand(node.upper) if node.upper is not None else None
            node.step = operand(node.step) if node.step is not None else None
        elif isinstance(node,ast.Call) and (
                ast.unparse(node.func) in COMPARATORS | {'abs','len','int','float','sum','min','max','all','any','sorted'}
                or ast.unparse(node.func).startswith('np.')):
            node.args = [operand(v) for v in node.args]
            node.keywords = [ast.keyword(arg=k.arg,value=operand(k.value)) for k in node.keywords]
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('any','all'):
            node.func.value = operand(node.func.value)
            node.args = [operand(v) for v in node.args]
            node.keywords = [ast.keyword(arg=k.arg,value=operand(k.value)) for k in node.keywords]
        elif isinstance(node,ast.Attribute) and node.attr in {'T','real','imag','shape','size','ndim'}:
            node.value = operand(node.value)
        elif target_only(node):
            pass
        else:
            # Only raw results and candidate function calls cross the boundary.
            # In particular a nested predicate is never an opaque operand.
            return capture(node)
        return node

    comparisons = [operand(a) for a in assertions]
    if wrapped:
        # Wrapper locals cannot be used by later tests. Keep reference-only
        # expressions (e.g. expected_output) exclusively in the root plan.
        # Preserve all candidate calls/side effects; remove only dead constants.
        needed = {n.id for op in operands for n in ast.walk(op)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        retained = []
        for node in reversed(child):
            assigned = {n.id for n in ast.walk(node)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            if node in constant_setup and not assigned & needed:
                continue
            retained.append(node)
            needed.difference_update(assigned)
            needed.update(n.id for n in ast.walk(node)
                          if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
        child = list(reversed(retained))
    # The sole custom target comparator in dev 10.4 and its helper are trusted.
    helpers = [n for n in child if isinstance(n,ast.FunctionDef) and n.name in {'isin','are_equivalent'}]
    def source_of(nodes):
        return ast.unparse(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])))
    expressions = [ast.unparse(ast.fix_missing_locations(v)) for v in operands]
    return {'compute':source_of(child), 'operands':expressions,
            'setup':source_of(constant_setup+helpers+root_setup),
            'assertions':[ast.unparse(ast.fix_missing_locations(v)) for v in comparisons]}


def compare_plan(plan, values, target):
    import contextlib
    import io
    import numpy as np
    try:
        from .test_util import cmp_tuple_or_list, are_dicts_close, are_csc_matrix_close
    except ImportError:
        from test_util import cmp_tuple_or_list, are_dicts_close, are_csc_matrix_close
    if type(values) is not list or len(values) != len(plan['operands']):
        return False
    namespace = dict(np=np, values=values, target=target, cmp_tuple_or_list=cmp_tuple_or_list,
                     are_dicts_close=are_dicts_close, are_csc_matrix_close=are_csc_matrix_close)
    # Suppress upstream helper diagnostics: even mismatching values stay private.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            exec(plan['setup'], namespace)
            return all(bool(eval(expr, namespace)) for expr in plan['assertions'])
        except Exception:
            return False
