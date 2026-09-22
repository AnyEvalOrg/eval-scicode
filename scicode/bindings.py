"""Inventory candidate module bindings without executing any candidate source.

This is name discovery only. Upstream tests are never rewritten or partitioned.
"""
import ast


def candidate_bindings(code, tests):
    referenced = {n.id for source in tests for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    bindings = {}

    class Inventory(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            bindings[node.name] = 'call'

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            if node.name in referenced:
                bindings[node.name] = 'call'

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store) and node.id in referenced:
                bindings[node.id] = 'get'

        # Imports are supplied from the pinned dependency source on the trusted
        # side. Never use candidate import statements as trusted setup.
        def visit_Import(self, node):
            pass

        visit_ImportFrom = visit_Import

        # Comprehension iteration variables are local to their own scope.
        visit_ListComp = visit_Import
        visit_SetComp = visit_Import
        visit_DictComp = visit_Import
        visit_GeneratorExp = visit_Import

        def visit_Lambda(self, node):
            pass

    try:
        Inventory().visit(ast.parse(code))
    except (SyntaxError, ValueError):
        # Invalid candidate source fails in the candidate, yielding a receipt.
        return {}
    return bindings
