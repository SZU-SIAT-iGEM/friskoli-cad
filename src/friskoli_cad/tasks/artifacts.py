"""Full-resolution final field artifacts, separate from JSON display frames."""
from pathlib import Path
import hashlib
import os
import numpy as np
from friskoli_cad.protocol.task_validation import canonical_bytes


def nonnegative_mean(values, axis=None):
    """Keep ordinary NumPy results; avoid overflowing sums of finite positives."""
    with np.errstate(over='ignore', invalid='ignore'):
        result = values.mean(axis=axis)
    if np.isfinite(result).all():
        return result
    scale = values.max(axis=axis, keepdims=True)
    normalized = np.divide(values, scale, out=np.zeros_like(values), where=scale > 0)
    return normalized.mean(axis=axis) * np.squeeze(scale, axis=axis)


def field_total_molecules(values, factor):
    with np.errstate(over='ignore', invalid='ignore'):
        total = float(values.sum() * factor)
    if np.isfinite(total):
        return total
    # The field kernel checks conversion before reduction, so a large raw
    # concentration sum may still have a finite physical inventory.
    from friskoli_cad.engine.local_fields import _mass
    return _mass(values, factor)


def file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def concentration_species(project):
    """Resolve concentration providers from registered ports for every profile."""
    from friskoli_cad.engine.profiles import registry_for_project
    registry = registry_for_project(project)
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    return {node['parameters'][port['species_parameter']]['value']
            for node in project['graph']['nodes']
            for port in manifests[(node['module_id'], node['module_version'])]['outputs'].values()
            if port['shape'] == 'field.scalar' and port['quantity'] == 'concentration'}


def final_field_estimate(submission):
    if not submission['output_plan'].get('include_final_fields', False):
        return 0
    project = submission['project']
    species = concentration_species(project)
    nx, ny, nz = project['domain']['counts_xyz']
    return 16384 + sum(nx * ny * nz * 8 + 4096 + len(name.encode('utf-8')) * 6 for name in species)


def write_final_fields(snapshot, step, destination, maximum):
    domain = snapshot.domain
    summary = {'artifact_id': 'final_fields', 'media_type': 'application/octet-stream',
               'step_index': step, 'time_s': snapshot.cell_frame['time_s'],
               'field_domain': {'geometry': domain.geometry,
                   'counts_xyz': [domain.nx, domain.ny, domain.nz],
                   'spacing_um_xyz': [domain.dx_um, domain.dy_um, domain.dz_um]}, 'fields': []}
    arrays = {}
    for index, (species, values) in enumerate(sorted(snapshot.concentration_fields.items())):
        if values.shape != domain.shape or values.dtype != np.float64 or not np.isfinite(values).all() or (values < 0).any() or snapshot.concentration_units[species] != 'uM':
            raise ValueError('Invalid final full-resolution concentration field')
        key = f'field_{index:04d}'
        arrays[key] = values
        summary['fields'].append({'species': species, 'array_key': key, 'unit': 'uM',
            'minimum': float(values.min()), 'maximum': float(values.max()), 'mean': float(nonnegative_mean(values)),
            'total_molecules': field_total_molecules(values, domain.molecules_per_uM_voxel)})
    arrays['metadata_utf8'] = np.frombuffer(canonical_bytes(summary), dtype=np.uint8)
    if sum(value.nbytes for value in arrays.values()) + len(arrays) * 4096 > maximum:
        raise ValueError('Final field artifact exceeds output_bytes')
    # No object arrays or pickle. Uncompressed NPZ streams each NPY member.
    with Path(destination).open('wb') as stream:
        np.savez(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    size, digest = file_digest(destination)
    if size > maximum:
        raise ValueError('Final field artifact exceeds output_bytes')
    return {**summary, 'bytes': size, 'sha256': digest}
