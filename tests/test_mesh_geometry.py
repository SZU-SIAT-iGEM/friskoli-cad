import numpy as np
import pytest

from friskoli_cad.science.processes import mesh_geometry, mesh_contains, mesh_voxel_mask


def voxel_shell(cubes):
    # Surface of a union of unit cubes, outward oriented and consistently welded.
    quads = [((-1, 0, 0), [(0,0,0),(0,0,1),(0,1,1),(0,1,0)]),
             ((1, 0, 0), [(1,0,0),(1,1,0),(1,1,1),(1,0,1)]),
             ((0,-1, 0), [(0,0,0),(1,0,0),(1,0,1),(0,0,1)]),
             ((0, 1, 0), [(0,1,0),(0,1,1),(1,1,1),(1,1,0)]),
             ((0, 0,-1), [(0,0,0),(0,1,0),(1,1,0),(1,0,0)]),
             ((0, 0, 1), [(0,0,1),(1,0,1),(1,1,1),(0,1,1)])]
    vertices, faces, ids = [], [], {}
    for cube in sorted(cubes):
        for direction, quad in quads:
            if tuple(a+b for a,b in zip(cube, direction)) in cubes: continue
            corners = []
            for offset in quad:
                point = tuple(a+b for a,b in zip(cube, offset))
                if point not in ids: ids[point] = len(vertices); vertices.append(point)
                corners.append(ids[point])
            faces.extend([[corners[0],corners[1],corners[2]], [corners[0],corners[2],corners[3]]])
    return np.asarray(vertices, float), np.asarray(faces, int)


def u_shell():
    return voxel_shell({(x,y,0) for x in range(7) for y in range(7) if x < 2 or x >= 5 or y < 2})


def test_nonconvex_shell_preserves_open_recess_and_volume():
    vertices, faces = u_shell()
    vertices, faces, volume = mesh_geometry(vertices, faces)
    assert volume == pytest.approx(34.)
    assert mesh_contains([[.5, 4., .5], [3.5, 4., .5]], vertices, faces).tolist() == [True, False]
    mask = mesh_voxel_mask(vertices, faces, (2, 14, 14), (.5, .5, .5))
    assert mask[0, 8, 1] and not mask[0, 8, 7]


def test_hollow_shell_has_inward_void_normals_and_subtracted_volume():
    outer, outer_faces = voxel_shell({(0,0,0)})
    inner, inner_faces = voxel_shell({(0,0,0)})
    vertices = np.vstack((outer * 5, inner * 3 + 1))
    faces = np.vstack((outer_faces, inner_faces[:, ::-1] + len(outer)))
    vertices, faces, volume = mesh_geometry(vertices, faces)
    assert volume == pytest.approx(125. - 27.)
    assert not mesh_contains([[2.5,2.5,2.5]], vertices, faces)[0]
    assert not mesh_voxel_mask(vertices, faces, (10,10,10), (.5,.5,.5))[5,5,5]
    with pytest.raises(ValueError, match='cavity normals'):
        mesh_geometry(vertices, np.vstack((outer_faces, inner_faces + len(outer))))


def test_inclusive_boundary_touch_and_triangle_aabb_is_only_broad_phase():
    vertices = np.array([[0.,0.,0.], [4.,0.,0.], [0.,4.,0.], [0.,0.,4.]])
    faces = np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]])
    vertices, faces, volume = mesh_geometry(vertices, faces)
    mask = mesh_voxel_mask(vertices, faces, (5,5,5), (1.,1.,1.))
    assert mask[0,0,4]  # Exact vertex contact at (4,0,0).
    assert not mask[3,3,3]  # Inside sloping face AABB, outside the triangle/solid.
    assert volume == pytest.approx(64/6)


def test_intersecting_closed_shells_are_rejected():
    a, af = voxel_shell({(0,0,0)}); b, bf = voxel_shell({(0,0,0)})
    with pytest.raises(ValueError, match='self-intersection'):
        mesh_geometry(np.vstack((a,b+.5)), np.vstack((af,bf+len(a))))


def test_vertex_nonmanifold_and_degenerate_open_duplicate_faces_rejected():
    # Two cubes touch at one welded vertex: edges alone look manifold.
    vertices, faces = voxel_shell({(0,0,0),(1,1,1)})
    with pytest.raises(ValueError, match='vertex link'): mesh_geometry(vertices, faces)
    vertices, faces = voxel_shell({(0,0,0)})
    with pytest.raises(ValueError, match='closed'): mesh_geometry(vertices, faces[:-1])
    with pytest.raises(ValueError, match='duplicate triangle'): mesh_geometry(vertices, np.vstack((faces,faces[0])))
    bad = faces.copy(); bad[0,1] = bad[0,0]
    with pytest.raises(ValueError, match='degenerate'): mesh_geometry(vertices, bad)


def test_disconnected_solids_and_translation_scaling():
    vertices, faces = voxel_shell({(0,0,0),(3,0,0)})
    translated = vertices + [1e5,-2e5,3e5]
    assert mesh_geometry(translated, faces, scale_um=2.)[2] == pytest.approx(16.)


def test_actual_nonconvex_module_keeps_recess_fluid_and_checkpoint():
    from test_modular_science import project, add
    from friskoli_cad.project import simulation_from_project
    from friskoli_cad.engine.modular_checkpoint import restore_checkpoint
    p = project(); p['species']['nutrient']['initial_concentration']['value'] = 1.
    for source in p['graph']['nodes']:
        if source['module_id'] == 'source.finite_local': source['parameters']['release_rate']['value'] = 0.
    vertices, faces = u_shell(); vertices = vertices * [10.,10.,1.6] + [110.,10.,.2]
    node = add(p, 'geometry.triangle_mesh', {'vertices_xyz': vertices.tolist(), 'faces': faces.tolist(), 'scale_um': 1.})
    sim = simulation_from_project(p)
    blocked = np.asarray(sim.fields.blocked).reshape(sim.world.grid.shape)
    assert blocked[0, 5, 11] and not blocked[0, 5, 14]
    field = np.asarray(sim.fields.concentrations_uM['nutrient']).reshape(sim.world.grid.shape)
    assert field[0,5,14] == 1. and field[0,5,11] == 0.
    assert sim.outputs[node]['volume'] == pytest.approx(5440.)
    sim.step(.01); restored = restore_checkpoint(p, sim.checkpoint())
    np.testing.assert_array_equal(sim.fields.blocked, restored.fields.blocked)
    sim.step(.01); restored.step(.01)
    np.testing.assert_array_equal(sim.fields.concentrations_uM['nutrient'], restored.fields.concentrations_uM['nutrient'])


def test_coplanar_overlap_is_rejected_and_cached_arrays_cannot_be_corrupted():
    a, af = voxel_shell({(0,0,0)}); b, bf = voxel_shell({(0,0,0)})
    with pytest.raises(ValueError, match='self-intersection'):
        mesh_geometry(np.vstack((a,b+[.5,0.,0.])), np.vstack((af,bf+len(a))))
    v, f, volume = mesh_geometry(a, af)
    v[:] = 0.; f[:] = 0
    v2, f2, volume2 = mesh_geometry(a, af)
    np.testing.assert_array_equal(v2, a); np.testing.assert_array_equal(f2, af)
    assert volume == volume2 == 1.


def test_folded_single_shell_and_reversed_face_are_rejected():
    vertices, faces = voxel_shell({(0,0,0)})
    folded = vertices.copy(); folded[0] = [1.5,.5,.5]
    with pytest.raises(ValueError, match='self-intersection'): mesh_geometry(folded, faces)
    reversed_face = faces.copy(); reversed_face[0] = reversed_face[0, ::-1]
    with pytest.raises(ValueError, match='consistent normals'): mesh_geometry(vertices, reversed_face)


def test_invalid_mesh_fails_registered_project_preflight():
    from test_modular_science import project, add
    from friskoli_cad.engine.science_extensions import modular_registry
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    p = project(); vertices, faces = voxel_shell({(0,0,0)})
    vertices[0] = [1.5,.5,.5]
    add(p, 'geometry.triangle_mesh', {'vertices_xyz': vertices.tolist(), 'faces': faces.tolist(), 'scale_um': 1.})
    registry = modular_registry()
    with pytest.raises(Exception, match='parameters.*self-intersection'):
        validate_modular_project(p, registry.manifests, registry)


def _original_pointwise_winding(points, vertices, faces):
    import math
    triangles = vertices[faces]
    result = []
    for point in points:
        a, b, c = (triangles[:, k] - point for k in range(3))
        la, lb, lc = (np.linalg.norm(v, axis=1) for v in (a, b, c))
        numerator = np.einsum('ij,ij->i', a, np.cross(b, c))
        denominator = la * lb * lc + np.einsum('ij,ij->i', a, b) * lc + np.einsum('ij,ij->i', b, c) * la + np.einsum('ij,ij->i', c, a) * lb
        result.append(abs(float(np.sum(2 * np.arctan2(numerator, denominator)))) > 2 * math.pi)
    return np.asarray(result, bool)


def test_batched_winding_matches_original_formula_on_random_points():
    vertices, faces = u_shell()
    points = np.random.default_rng(730).uniform([-1.,-1.,-.5], [8.,8.,1.5], (2000,3))
    np.testing.assert_array_equal(mesh_contains(points, vertices, faces), _original_pointwise_winding(points, vertices, faces))
    # Exercise face chunking too; repeated winding sheets are used solely to
    # exceed the work budget without constructing a huge geometric fixture.
    vertices, faces = voxel_shell({(0,0,0)})
    many_faces = np.tile(faces, (6000,1))
    points = np.array([[.37,.41,.53],[1.5,1.3,.7],[-.8,.4,.5]])
    np.testing.assert_array_equal(mesh_contains(points, vertices, many_faces), _original_pointwise_winding(points, vertices, many_faces))


def test_voxel_winding_excludes_far_outside_mesh_bounds(monkeypatch):
    from friskoli_cad.science import processes
    vertices, faces = voxel_shell({(0,0,0)}); vertices += 10.
    original = processes.mesh_contains; inspected = []
    def record(points, v, f):
        inspected.extend(np.asarray(points).tolist())
        return original(points, v, f)
    monkeypatch.setattr(processes, 'mesh_contains', record)
    mask = processes.mesh_voxel_mask(vertices, faces, (20,20,20), (1.,1.,1.))
    assert 0 < len(inspected) < mask.size
    points = np.asarray(inspected)
    assert np.all(points + .5 >= 10.) and np.all(points - .5 <= 11.)
    assert mask[10,10,10] and not mask[0,0,0]
