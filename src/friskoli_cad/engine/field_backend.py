"""Execution adapters for the same conservative float64 finite-volume stencil.

Backend choice is explicit run configuration, never selected by example ID.
CUDA accelerates field array work; cell physiology and keyed RNG stay on CPU.
"""
from functools import lru_cache
import importlib.util
import os
from pathlib import Path
import warnings
import numpy as np

CPU = 'numpy-cpu'
CUDA = 'numpy-cupy-cuda'
_dll_handles = []


@lru_cache(maxsize=1)
def _cupy():
    # A locally installed PyTorch CUDA distribution can provide NVRTC on Windows.
    # No PyTorch import is needed, and the numerical code remains independent.
    bundled_runtime = False
    if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
        spec = importlib.util.find_spec('torch')
        if spec and spec.origin:
            directory = Path(spec.origin).parent / 'lib'
            if directory.is_dir():
                _dll_handles.append(os.add_dll_directory(str(directory)))
                bundled_runtime = True
    with warnings.catch_warnings():
        if bundled_runtime:
            warnings.filterwarnings('ignore', message='CUDA path could not be detected.*', category=UserWarning)
        import cupy
    if cupy.cuda.runtime.getDeviceCount() < 1:
        raise RuntimeError('No CUDA device is available')
    return cupy


@lru_cache(maxsize=1)
def available_backends():
    try:
        validate_backend(CUDA)
    except Exception:
        return [CPU]
    return [CPU, CUDA]


def validate_backend(name):
    if name not in (CPU, CUDA):
        raise ValueError('Unknown field execution backend')
    if name == CUDA:
        _probe_cuda()


@lru_cache(maxsize=1)
def _probe_cuda():
    """Capability means a compiled and executed float64 kernel, not import alone."""
    cp = _cupy()
    source = cp.asarray(np.array([1., 0.], dtype=np.float64))
    target = cp.empty_like(source)
    mask = cp.zeros(2, dtype=cp.bool_)
    _kernel()((1,), (32,), (source, target, mask, np.int32(2), np.int32(1), np.int32(1),
                          np.float64(.25), np.float64(0), np.float64(0)))
    if not np.array_equal(cp.asnumpy(target), np.array([.75, .25])):
        raise RuntimeError('CUDA float64 stencil capability check failed')
    return True


def memory_available_bytes(name):
    """Device resource admission; CPU memory is checked by the task service."""
    validate_backend(name)
    return int(_cupy().cuda.runtime.memGetInfo()[0]) if name == CUDA else None


def backend_environment(name):
    """Execution environment recorded for the explicitly selected GPU adapter."""
    validate_backend(name)
    if name == CPU:
        return {}
    cp = _cupy()
    device = cp.cuda.Device()
    properties = cp.cuda.runtime.getDeviceProperties(device.id)
    label = properties['name']
    return {'cupy_version': cp.__version__,
            'cuda_runtime_version': int(cp.cuda.runtime.runtimeGetVersion()),
            'driver_version': int(cp.cuda.runtime.driverGetVersion()),
            'device_name': label.decode('utf-8') if isinstance(label, bytes) else str(label),
            'compute_capability': str(device.compute_capability)}


_STENCIL = r'''
extern "C" __global__ void no_flux_step(
    const double* u, double* v, const bool* blocked,
    const int nx, const int ny, const int nz,
    const double ax, const double ay, const double az) {
    const long long i = (long long)blockDim.x * blockIdx.x + threadIdx.x;
    const long long n = (long long)nx * ny * nz;
    if (i >= n) return;
    if (blocked[i]) { v[i] = 0.0; return; }
    const int x = i % nx, y = (i / nx) % ny, z = i / ((long long)nx * ny);
    const long long plane = (long long)nx * ny;
    const double c = u[i];
    double rate = 0.0;
    if (x+1 < nx && !blocked[i+1]) rate += (u[i+1]-c)*ax;
    if (x > 0 && !blocked[i-1]) rate -= (c-u[i-1])*ax;
    if (y+1 < ny && !blocked[i+nx]) rate += (u[i+nx]-c)*ay;
    if (y > 0 && !blocked[i-nx]) rate -= (c-u[i-nx])*ay;
    if (z+1 < nz && !blocked[i+plane]) rate += (u[i+plane]-c)*az;
    if (z > 0 && !blocked[i-plane]) rate -= (c-u[i-plane])*az;
    v[i] = c + rate;
}
'''


@lru_cache(maxsize=1)
def _kernel():
    # Match NumPy's separate multiplication and addition, without fast math/FMA.
    return _cupy().RawKernel(_STENCIL, 'no_flux_step', options=('--fmad=false',))


def diffuse_cuda(field, grid, coefficient, dt, blocked, substeps):
    """One host/device round trip for every CFL substep of a field step."""
    cp = _cupy()
    current = cp.asarray(field, dtype=cp.float64, order='C')
    target = cp.empty_like(current)
    mask = cp.asarray(blocked, dtype=cp.bool_, order='C')
    step = dt / substeps
    args = (np.int32(grid.nx), np.int32(grid.ny), np.int32(grid.nz),
            np.float64(coefficient*step/grid.dx_um**2),
            np.float64(coefficient*step/grid.dy_um**2),
            np.float64(coefficient*step/grid.dz_um**2))
    for _ in range(substeps):
        _kernel()(((grid.voxel_count+255)//256,), (256,), (current, target, mask, *args))
        current, target = target, current
    result = cp.asnumpy(current)
    if not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError('CUDA diffusion produced nonfinite/negative concentration')
    return result
