"""Explicit exploratory processes, with conserved transfers and declared units.

These numerical laws are not strain calibrations. Their assumptions and primary
sources are recorded in docs/science/modular-processes.md.
"""
from __future__ import annotations

import math
import numpy as np


def nonnegative(value, name="value"):
    array = np.asarray(value, dtype=float)
    if not np.isfinite(array).all() or np.any(array < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return array


def lowpass(previous, value, dt_s, tau_s):
    if tau_s <= 0 or dt_s < 0:
        raise ValueError("lowpass requires tau > 0 and dt >= 0")
    return np.asarray(previous) + (-math.expm1(-dt_s / tau_s)) * (np.asarray(value) - previous)


def allocate_inventory(reserve, accepted, dt_s, maintenance_rate, growth_request):
    """One accepted transfer; maintenance first, growth next, residual last."""
    reserve, accepted = nonnegative(reserve), nonnegative(accepted)
    available = reserve + accepted
    maintenance = np.minimum(available, nonnegative(maintenance_rate) * dt_s)
    growth = np.minimum(available - maintenance, nonnegative(growth_request))
    residual = available - maintenance - growth
    unmet = np.where(np.asarray(maintenance_rate) > 0,
                     dt_s - maintenance / np.maximum(maintenance_rate, np.finfo(float).tiny), 0.)
    return residual, maintenance, growth, unmet


def stokes_einstein_scale(diffusivity, temperature_K, reference_temperature_K,
                          viscosity_Pa_s, reference_viscosity_Pa_s):
    if min(temperature_K, reference_temperature_K, viscosity_Pa_s, reference_viscosity_Pa_s) <= 0:
        raise ValueError("temperature and viscosity must be positive")
    return nonnegative(diffusivity) * temperature_K / reference_temperature_K * reference_viscosity_Pa_s / viscosity_Pa_s


def exchange(concentration, target, rate_s, dt_s, mask=None):
    """Exact first-order reservoir exchange; signed delta is external matter."""
    before = nonnegative(concentration)
    target = nonnegative(target)
    if rate_s < 0 or dt_s < 0:
        raise ValueError("exchange rates and duration must be nonnegative")
    after = before + (target - before) * -math.expm1(-rate_s * dt_s)
    if mask is not None:
        after = np.where(mask, after, before)
    return after, after - before


def upwind_advection(concentration, velocity_xyz, spacing_xyz, dt_s, *, periodic=False, blocked=None):
    """Conservative donor-cell finite volumes; no-flux or periodic boundaries.

    Velocity is a uniform prescribed XYZ vector (um/s), concentration uses ZYX.
    Internal faces touching solids have zero flux. Unsplit CFL <= 1 per substep.
    """
    value = nonnegative(concentration).copy()
    velocity = np.asarray(velocity_xyz, dtype=float)
    spacing = np.asarray(spacing_xyz, dtype=float)
    if value.ndim != 3 or velocity.shape != (3,) or spacing.shape != (3,):
        raise ValueError("advection requires ZYX volume and XYZ vectors")
    if not np.isfinite(velocity).all() or np.any(spacing <= 0) or dt_s < 0:
        raise ValueError("invalid advection coefficients")
    mask = np.zeros(value.shape, bool) if blocked is None else np.asarray(blocked, bool).reshape(value.shape)
    count = max(1, math.ceil(dt_s * float(np.sum(np.abs(velocity) / spacing))))
    if count > 100000:
        raise ValueError("advection substep budget exceeded")
    for _ in range(count):
        updated = value.copy()
        for xyz, speed in enumerate(velocity):
            axis = 2 - xyz
            neighbour = np.roll(value, -1, axis)
            flux = (speed * (value if speed >= 0 else neighbour)) * dt_s / count / spacing[xyz]
            flux[mask | np.roll(mask, -1, axis)] = 0.
            if not periodic:
                end = [slice(None)] * 3
                end[axis] = -1
                flux[tuple(end)] = 0.
            updated += np.roll(flux, 1, axis) - flux
        if np.min(updated) < -1e-12 * max(1., float(np.max(value))):
            raise ValueError("advection lost positivity")
        value = np.maximum(updated, 0.)
    return value


def _segment_triangle_hits(start, end, triangle, tolerance):
    """Return intersection endpoints, including coplanar segment overlap."""
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    normal /= np.linalg.norm(normal)
    first, last = float((start - triangle[0]) @ normal), float((end - triangle[0]) @ normal)
    if (first > tolerance and last > tolerance) or (first < -tolerance and last < -tolerance): return []
    axis = int(np.argmax(np.abs(normal)))
    axes = [i for i in range(3) if i != axis]
    tri = triangle[:, axes]
    def cross(a, b): return float(a[0] * b[1] - a[1] * b[0])
    sign = 1. if cross(tri[1] - tri[0], tri[2] - tri[0]) > 0 else -1.
    if abs(first) <= tolerance and abs(last) <= tolerance:
        low, high = 0., 1.
        for i in range(3):
            edge = tri[(i + 1) % 3] - tri[i]
            a = sign * cross(edge, start[axes] - tri[i])
            b = sign * cross(edge, end[axes] - tri[i])
            eps = tolerance * np.linalg.norm(edge)
            if a < -eps and b < -eps: return []
            if a < 0 <= b: low = max(low, -a / (b - a))
            elif b < 0 <= a: high = min(high, a / (a - b))
        if low > high + 1e-12: return []
        return [start + low * (end - start), start + high * (end - start)]
    if abs(first - last) <= np.finfo(float).tiny: return []
    fraction = first / (first - last)
    if fraction < -1e-12 or fraction > 1 + 1e-12: return []
    point = start + np.clip(fraction, 0., 1.) * (end - start)
    for i in range(3):
        edge = tri[(i + 1) % 3] - tri[i]
        if sign * cross(edge, point[axes] - tri[i]) < -tolerance * np.linalg.norm(edge): return []
    return [point]


from functools import lru_cache


@lru_cache(maxsize=4)
def _validated_mesh_arrays(vertex_bytes, vertex_shape, face_bytes, face_shape, face_dtype, scale_um):
    vertices = np.frombuffer(vertex_bytes, dtype=np.float64).reshape(vertex_shape)
    faces = np.frombuffer(face_bytes, dtype=np.dtype(face_dtype)).reshape(face_shape)
    physical, indices, volume = _mesh_geometry_uncached(vertices, faces, scale_um=scale_um)
    physical.setflags(write=False); indices.setflags(write=False)
    return physical, indices, volume


def mesh_geometry(vertices_xyz, faces, *, scale_um=1.):
    """Validate exactly keyed immutable geometry; retain at most four meshes.

    Cache entries cannot be mutated through returned arrays. Parameter changes
    always produce another exact key and repeat full geometric validation.
    """
    vertices, indices = np.asarray(vertices_xyz, dtype=np.float64), np.asarray(faces)
    if indices.dtype.kind not in 'iu': raise ValueError('mesh faces must be integer triangles')
    physical, indexed, volume = _validated_mesh_arrays(vertices.tobytes(), vertices.shape,
        indices.tobytes(), indices.shape, indices.dtype.str, float(scale_um))
    return physical.copy(), indexed.copy(), volume


def _mesh_geometry_uncached(vertices_xyz, faces, *, scale_um=1.):
    """Validate finite embedded oriented closed triangle shells in physical units.

    Nonconvex shells, disjoint solids, and inward-oriented cavity shells are
    accepted. Vertex links, oriented edge incidence, pairwise intersections,
    and shell nesting are checked before any physical field is allocated.
    """
    vertices = np.asarray(vertices_xyz, dtype=float) * scale_um
    indices = np.asarray(faces)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4 or not np.isfinite(vertices).all():
        raise ValueError('mesh vertices must be finite XYZ rows')
    if indices.ndim != 2 or indices.shape[1] != 3 or not np.issubdtype(indices.dtype, np.integer):
        raise ValueError('mesh faces must be integer triangles')
    if len(indices) < 4: raise ValueError('closed mesh requires at least four triangles')
    if not math.isfinite(scale_um) or scale_um <= 0 or np.any(indices < 0) or np.any(indices >= len(vertices)):
        raise ValueError('mesh scale or indices invalid')
    if len(np.unique(vertices, axis=0)) != len(vertices): raise ValueError('duplicate mesh vertices must be welded')
    if set(indices.flat) != set(range(len(vertices))): raise ValueError('unused mesh vertex')
    if len({tuple(sorted(face)) for face in indices}) != len(indices): raise ValueError('duplicate triangle')
    span = float(np.ptp(vertices, axis=0).max())
    tolerance = max(span * 1e-10, np.max(np.abs(vertices)) * np.finfo(float).eps * 32)
    if span <= 0: raise ValueError('degenerate mesh extent')
    triangles = vertices[indices]
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    if np.any(np.linalg.norm(normal, axis=1) <= tolerance * span): raise ValueError('degenerate triangle')
    edges, vertex_links = {}, {i: {} for i in range(len(vertices))}
    for face_id, (a, b, c) in enumerate(indices):
        for i, j in ((a, b), (b, c), (c, a)):
            edges.setdefault(tuple(sorted((int(i), int(j)))), []).append((face_id, 1 if i < j else -1))
        for vertex, i, j in ((a, b, c), (b, c, a), (c, a, b)):
            vertex_links[vertex].setdefault(i, set()).add(j); vertex_links[vertex].setdefault(j, set()).add(i)
    if any(len(value) != 2 or value[0][1] == value[1][1] for value in edges.values()):
        raise ValueError('mesh must be closed with consistent normals and manifold edges')
    for link in vertex_links.values():
        if any(len(neighbors) != 2 for neighbors in link.values()): raise ValueError('non-manifold vertex link')
        reached, pending = set(), [next(iter(link))]
        while pending:
            vertex = pending.pop()
            if vertex not in reached: reached.add(vertex); pending.extend(link[vertex] - reached)
        if len(reached) != len(link): raise ValueError('non-manifold disconnected vertex link')
    # Sweep triangle bounding intervals; narrow phase permits only the shared
    # topological vertex/edge, never overlapping interiors or unrelated contact.
    lower, upper = triangles.min(axis=1), triangles.max(axis=1)
    order = np.argsort(lower[:, 0], kind='stable')
    for rank, first in enumerate(order):
        for second in order[rank + 1:]:
            if lower[second, 0] > upper[first, 0] + tolerance: break
            if np.any(lower[second] > upper[first] + tolerance) or np.any(lower[first] > upper[second] + tolerance): continue
            shared = sorted(set(indices[first]) & set(indices[second]))
            hits = []
            for source, target in ((triangles[first], triangles[second]), (triangles[second], triangles[first])):
                for k in range(3): hits.extend(_segment_triangle_hits(source[k], source[(k + 1) % 3], target, tolerance))
            for point in hits:
                if len(shared) == 1 and np.linalg.norm(point - vertices[shared[0]]) <= 4 * tolerance: continue
                if len(shared) == 2:
                    a, b = vertices[shared]; edge = b - a
                    fraction = np.clip(float((point - a) @ edge / (edge @ edge)), 0., 1.)
                    if np.linalg.norm(point - a - fraction * edge) <= 4 * tolerance: continue
                raise ValueError('self-intersection or non-topological surface contact')
    adjacency = {i: set() for i in range(len(indices))}
    for pair in edges.values():
        a, b = pair[0][0], pair[1][0]; adjacency[a].add(b); adjacency[b].add(a)
    components, unseen = [], set(adjacency)
    while unseen:
        pending, component = [min(unseen)], []
        while pending:
            face = pending.pop()
            if face not in unseen: continue
            unseen.remove(face); component.append(face); pending.extend(adjacency[face] & unseen)
        components.append(component)
    volume = 0.
    for component in components:
        tri = triangles[component]; reference = tri[0, 0]
        relative = tri - reference
        signed = float(np.einsum('ij,ij->i', relative[:, 0], np.cross(relative[:, 1], relative[:, 2])).sum() / 6)
        depth = sum(bool(mesh_contains([reference], vertices, indices[other])[0]) for other in components if other is not component)
        if abs(signed) <= tolerance * span ** 2 or (signed > 0) != (depth % 2 == 0):
            raise ValueError('shell normals must face outward from solid, including inward cavity normals')
        volume += signed
    if volume <= 0: raise ValueError('mesh must enclose positive solid volume')
    return vertices, indices, volume


def mesh_contains(points_xyz, vertices, faces):
    """Solid-angle winding, vectorized within a 16 MiB working-array budget.

    Budget 32 float64 intermediates per point/triangle pair. Ordinary meshes
    retain the original whole-face summation order for every point; unusually
    large meshes also chunk faces instead of allocating an unbounded slab.
    """
    points = np.asarray(points_xyz, float).reshape(-1, 3)
    triangles = np.asarray(vertices)[np.asarray(faces)]
    inside = np.zeros(len(points), bool)
    if not len(points) or not len(triangles): return inside
    pair_budget = (16 * 1024 * 1024) // (32 * np.dtype(np.float64).itemsize)
    face_batch = min(len(triangles), pair_budget)
    point_batch = max(1, pair_budget // face_batch)
    for start in range(0, len(points), point_batch):
        batch = points[start:start + point_batch]
        angles = np.zeros(len(batch), float)
        for offset in range(0, len(triangles), face_batch):
            tri = triangles[offset:offset + face_batch]
            a, b, c = (tri[None, :, k, :] - batch[:, None, :] for k in range(3))
            la, lb, lc = (np.linalg.norm(v, axis=2) for v in (a, b, c))
            numerator = np.einsum('pfi,pfi->pf', a, np.cross(b, c))
            denominator = (la * lb * lc + np.einsum('pfi,pfi->pf', a, b) * lc
                + np.einsum('pfi,pfi->pf', b, c) * la + np.einsum('pfi,pfi->pf', c, a) * lb)
            angles += np.sum(2 * np.arctan2(numerator, denominator), axis=1)
        inside[start:start + len(batch)] = np.abs(angles) > 2 * math.pi
    return inside


def mesh_voxel_mask(vertices, faces, shape_zyx, spacing_xyz):
    """Solid-centre winding plus inclusive triangle/box separating-axis test.

    Face AABBs only prune candidates. They never turn whole concave regions
    into solid. A box touched at a face, edge, or corner is conservatively solid.
    """
    spacing = np.asarray(spacing_xyz, float); shape = tuple(shape_zyx)
    z, y, x = np.indices(shape)
    centers = np.stack(((x + .5) * spacing[0], (y + .5) * spacing[1], (z + .5) * spacing[2]), axis=-1)
    flat = centers.reshape(-1, 3); half = spacing / 2
    mask = np.zeros(len(flat), bool)
    # Only voxels intersecting the overall mesh bounds can contain solid or
    # touch its surface. Chunk indexed gathers as well as winding intermediates.
    overall_lower, overall_upper = np.min(vertices, axis=0), np.max(vertices, axis=0)
    tolerance = max(float(np.ptp(vertices, axis=0).max()), float(spacing.max())) * 1e-10
    candidates = np.flatnonzero(np.all((flat + half >= overall_lower - tolerance)
        & (flat - half <= overall_upper + tolerance), axis=1))
    for start in range(0, len(candidates), 65536):
        selected = candidates[start:start + 65536]
        mask[selected] = mesh_contains(flat[selected], vertices, faces)
    basis = np.eye(3)
    for triangle in np.asarray(vertices)[np.asarray(faces)]:
        candidates = np.flatnonzero(np.all((flat + half >= triangle.min(axis=0) - tolerance) & (flat - half <= triangle.max(axis=0) + tolerance), axis=1) & ~mask)
        if not len(candidates): continue
        edges = np.roll(triangle, -1, axis=0) - triangle
        axes = [*basis, np.cross(edges[0], edges[1]), *(np.cross(edge, direction) for edge in edges for direction in basis)]
        intersects = np.ones(len(candidates), bool)
        for axis in axes:
            norm = float(np.linalg.norm(axis))
            if norm <= np.finfo(float).tiny: continue
            axis = axis / norm
            projection = triangle @ axis
            center_projection = flat[candidates] @ axis
            radius = half @ np.abs(axis)
            intersects &= (projection.min() <= center_projection + radius + tolerance) & (projection.max() >= center_projection - radius - tolerance)
        mask[candidates[intersects]] = True
    return mask.reshape(shape)


def matched_trilinear_weights(positions_xyz, shape_zyx, spacing_xyz):
    """Cell-centred clipped trilinear weights, shared by sample and deposit."""
    positions = np.asarray(positions_xyz, float)
    limits = np.asarray(shape_zyx[::-1])
    q = positions / np.asarray(spacing_xyz) - .5
    base, fraction = np.floor(q).astype(int), q - np.floor(q)
    result = []
    for lower, f in zip(base, fraction):
        weights = {}
        for x in (0, 1):
            for y in (0, 1):
                for z in (0, 1):
                    offset = np.asarray((x, y, z))
                    index = np.clip(lower + offset, 0, limits - 1)
                    flat = int((index[2] * limits[1] + index[1]) * limits[0] + index[0])
                    weight = float(np.prod(np.where(offset, f, 1 - f)))
                    weights[flat] = weights.get(flat, 0.) + weight
        result.append(weights)
    return result


def schedule_amount(events, time_s, dt_s, *, initialize=False):
    """Integrate piecewise constant rates and pulses on (t,t+dt].

    Each event is {kind: pulse|rate, time_s, amount_molecules} or
    {kind: rate,start_s,end_s,rate_molecules_s}. Time zero belongs only to init.
    """
    end = time_s + dt_s
    total = 0.
    for event in events:
        if event['kind'] == 'pulse':
            t = float(event['time_s'])
            amount = float(nonnegative(event['amount_molecules']))
            if t < 0 or not math.isfinite(t):
                raise ValueError('pulse time must be nonnegative and finite')
            if (initialize and t == 0) or (not initialize and time_s < t <= end):
                total += amount
        elif event['kind'] == 'rate':
            a, b = float(event['start_s']), float(event['end_s'])
            rate = float(nonnegative(event['rate_molecules_s']))
            if not 0 <= a < b or not math.isfinite(b):
                raise ValueError('rate interval must be finite and increasing')
            if not initialize:
                total += rate * max(0., min(end, b) - max(time_s, a))
        else:
            raise ValueError('unknown schedule event')
    return total


def validate_property_catalog(catalog):
    """Unknown is explicit null; every known value has conditions and evidence."""
    if not isinstance(catalog, (list, tuple)):
        raise ValueError('property catalog must be a list')
    required = {'id', 'quantity', 'unit', 'value', 'conditions', 'source', 'uncertainty'}
    seen = set()
    for item in catalog:
        if set(item) != required or item['id'] in seen:
            raise ValueError('property record keys or ID invalid')
        seen.add(item['id'])
        if not all(isinstance(item[k], str) and item[k] for k in ('id', 'quantity', 'unit', 'source')):
            raise ValueError('property identity, unit and source required')
        if not isinstance(item['conditions'], dict) or not item['conditions']:
            raise ValueError('property applicability conditions required')
        if item['value'] is not None:
            if not np.isfinite(item['value']):
                raise ValueError('known property must be finite')
            if item['uncertainty'] is not None:
                nonnegative(item['uncertainty'], 'property uncertainty')
    return catalog


def cellulose_hydrolysis(agu_units, enzyme_copies, rate_per_enzyme_s, dt_s, *, route='glucose'):
    """Lumped AGU balance, with explicit solvent-water stoichiometry.

    Infinite-chain AGU convention excludes terminal chain corrections. One AGU
    plus one water makes one glucose; two AGU plus one water make cellobiose.
    """
    available = float(nonnegative(agu_units))
    converted = min(available, float(nonnegative(enzyme_copies)) * float(nonnegative(rate_per_enzyme_s)) * dt_s)
    if route not in ('glucose', 'cellobiose'):
        raise ValueError('unknown cellulose reaction product')
    product = converted if route == 'glucose' else converted / 2
    return {'remaining_agu': available - converted, 'consumed_agu': converted,
            'product_molecules': product, 'water_consumed_molecules': product}


def conversion_fraction(converted, initial, supplied=0.):
    denominator = float(nonnegative(initial)) + float(nonnegative(supplied))
    return None if denominator == 0 else float(nonnegative(converted)) / denominator


def compensated_amount(high, low, delta):
    """Add a signed transfer without losing its low component to a large stock."""
    from fractions import Fraction
    high, low, delta = np.broadcast_arrays(high, low, delta)
    result, correction = np.empty(high.shape), np.empty(high.shape)
    for i in np.ndindex(high.shape):
        exact = Fraction(float(high[i])) + Fraction(float(low[i])) + Fraction(float(delta[i]))
        if exact < 0: raise ValueError('Transfer exceeds corrected stock')
        result[i] = float(exact)
        correction[i] = float(exact - Fraction(float(result[i])))
    return result, correction


def advance_compensated_reserve(reserve, accepted, dt_s, rate, correction):
    """Two-component maintenance stock; rounding bound follows the low part.

    A zero arrival must not demand zero rounding error from maintenance debit.
    The legacy reserve law remains unchanged in its original execution profile.
    """
    from fractions import Fraction
    from .survival import ReserveReadout
    reserve, accepted, correction = np.broadcast_arrays(reserve, accepted, correction)
    high, low, used = np.empty(reserve.shape), np.empty(reserve.shape), np.empty(reserve.shape)
    demand = Fraction(float(rate * dt_s))
    for i in np.ndindex(reserve.shape):
        total = Fraction(float(reserve[i])) + Fraction(float(correction[i])) + Fraction(float(accepted[i]))
        if total < 0: raise ValueError('Negative corrected inventory')
        exact_used = min(total, demand)
        used[i] = float(exact_used)
        if Fraction(float(used[i])) > exact_used: used[i] = math.nextafter(float(used[i]), 0.)
        remainder = total - Fraction(float(used[i]))
        high[i] = float(remainder)
        low[i] = float(remainder - Fraction(float(high[i])))
        residual = abs(remainder - Fraction(float(high[i])) - Fraction(float(low[i])))
        transfer_scale = max(float(accepted[i]), float(used[i]))
        budget = 1e-10 * transfer_scale + 8 * math.ulp(transfer_scale)
        if residual > Fraction(budget): raise ValueError('Two-component inventory exceeds actual transfer precision budget')
    return ReserveReadout(high, used, np.zeros_like(high) if rate == 0 else np.maximum(0., dt_s - used / rate), low)
