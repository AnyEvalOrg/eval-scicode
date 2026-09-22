"""Authored finite-difference reference for the 13.12 object-transport fixture.

Cell-centred octant, second-order centred interior stencils, reflection at
inner faces, outgoing-wave outer faces, and three-stage iterative CN evolution.
Test-split ground truth is unavailable; fixture targets are computed by running
this reference directly, then the unchanged upstream tests run through RPC.
"""
import numpy as np


class Maxwell:
    def __init__(self, n_grid, x_out):
        self.delta = x_out / n_grid
        coordinates = (np.arange(n_grid) - 0.5) * self.delta
        self.x, self.y, self.z = np.meshgrid(*[coordinates]*3, indexing='ij')
        self.r = np.sqrt(self.x**2 + self.y**2 + self.z**2)
        self.t = 0.
        self.n_vars = 7
        for name in ('E_x', 'E_y', 'E_z', 'A_x', 'A_y', 'A_z', 'phi'):
            setattr(self, name, np.zeros((n_grid,)*3))


def partials(field, delta):
    return np.gradient(field, delta, edge_order=2)


def second(field, i, j, delta):
    result = np.zeros_like(field)
    center = [slice(1, -1)] * 3
    if i == j:
        plus, minus = center.copy(), center.copy()
        plus[i], minus[i] = slice(2, None), slice(None, -2)
        result[tuple(center)] = (field[tuple(plus)] - 2*field[tuple(center)]
                                 + field[tuple(minus)]) / delta**2
    else:
        for si in (-1, 1):
            for sj in (-1, 1):
                offset = center.copy()
                offset[i] = slice(2, None) if si == 1 else slice(None, -2)
                offset[j] = slice(2, None) if sj == 1 else slice(None, -2)
                result[tuple(center)] += si*sj*field[tuple(offset)] / (4*delta**2)
    return result


def derivatives(maxwell, fields):
    electric, potential, phi = fields[:3], fields[3:6], fields[6]
    delta = maxwell.delta
    e_dot = [sum(second(potential[j], i, j, delta) for j in range(3))
             - sum(second(potential[i], j, j, delta) for j in range(3)) for i in range(3)]
    a_dot = [-e - d for e, d in zip(electric, partials(phi, delta))]
    phi_dot = -sum(partials(potential[i], delta)[i] for i in range(3))
    result = e_dot + a_dot + [phi_dot]
    # Vector reflection on the coordinate planes; scalar potential is even.
    for k, (field, derivative) in enumerate(zip(fields, result)):
        grad = partials(field, delta)
        outgoing = -(field + maxwell.x*grad[0] + maxwell.y*grad[1]
                     + maxwell.z*grad[2]) / maxwell.r
        for axis in range(3):
            inner, adjacent, outer = [slice(None)]*3, [slice(None)]*3, [slice(None)]*3
            inner[axis], adjacent[axis], outer[axis] = 0, 1, -1
            parity = -1 if k < 6 and k % 3 == axis else 1
            derivative[tuple(inner)] = parity * derivative[tuple(adjacent)]
            derivative[tuple(outer)] = outgoing[tuple(outer)]
    return result


def stepper(maxwell, fields, courant, t_const):
    state = [field.copy() for field in fields]
    remaining = t_const
    while remaining > np.finfo(float).eps:
        dt = min(courant*maxwell.delta, remaining)
        initial_dot = derivatives(maxwell, state)
        predicted = [f + dt*d for f, d in zip(state, initial_dot)]
        for _ in range(2):
            final_dot = derivatives(maxwell, predicted)
            predicted = [f + dt*(a+b)/2 for f, a, b in zip(state, initial_dot, final_dot)]
        state = predicted
        maxwell.t += dt
        remaining -= dt
    for name, field in zip(('E_x', 'E_y', 'E_z', 'A_x', 'A_y', 'A_z', 'phi'), state):
        setattr(maxwell, name, field)
    return maxwell.t, state


def check_constraint(maxwell):
    divergence = sum(partials(field, maxwell.delta)[axis]
                     for axis, field in enumerate((maxwell.E_x, maxwell.E_y, maxwell.E_z)))
    interior = divergence[1:-1, 1:-1, 1:-1]
    return np.sqrt(np.sum(interior**2) * maxwell.delta**3)
