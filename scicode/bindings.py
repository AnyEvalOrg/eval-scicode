"""Inventory candidate bindings INSIDE the resource-limited candidate worker.

This is name discovery only. Upstream tests are never rewritten or partitioned.
"""
import ast


def candidate_bindings(code):
    bindings = {}

    class Inventory(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            bindings[node.name] = 'call'

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            bindings[node.name] = 'get'

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store):
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

    # Parsing/visitor failures terminate candidate initialization. The supervisor
    # signs INCORRECT; the host never inspects or parses this source.
    Inventory().visit(ast.parse(code))
    return bindings
