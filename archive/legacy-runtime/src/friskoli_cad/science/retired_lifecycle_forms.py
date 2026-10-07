"""已退休的有状态科学实现（modular-spatial-v1 之前）。

从 science/physiology.py、science/survival.py、science/chemotaxis.py 提取。
这些函数把生长、拷贝数、健康度、分裂、维持储备和 tumble 风险率写成
"读入当前状态 → 返回下一状态" 的形式，由旧 profile 的执行器按菌群直接调用。

统一执行规范之后，同一批公式改由 ``science/processes.py`` 提供纯函数、
``engine/science_adapters.py`` 包装成注册模块提案，状态归属移交给
``engine/modular_runtime.py`` 的原子提交。旧形式因此失去调用方。

本文件只作追溯，不被任何模块导入，也不参与当前执行。
留在原模块的：growth_system 仍在用的 ``GrowthReadout``、``capsule_volume_um3``、
``capsule_geometry_from_volume``，以及 ``ReserveReadout``、``advance_starvation``。

原文依赖（归档不执行，仅记录）：
    import numpy as np
    from dataclasses import dataclass, fields
    from fractions import Fraction
    import math
    from science.pts import _value, _align, _product
    from science.chemotaxis import motor_bias, _signed
"""

# ---- 原 science/physiology.py ----

def advance_growth(intracellular_molecules, volume_um3, dt_s, *, policy,
                   max_growth_per_min, volume_yield_um3_molecule, half_saturation_uM=None):
    stock, volume = _align(_value("intracellular_molecules", intracellular_molecules),
                           _value("volume_um3", volume_um3, positive=True))
    dt = _value("dt_s", dt_s, scalar=True) / 60
    maximum = _value("max_growth_per_min", max_growth_per_min, scalar=True)
    yield_v = _value("volume_yield_um3_molecule", volume_yield_um3_molecule, positive=True, scalar=True)
    if policy == "rebuilt_monod":
        half = _value("half_saturation_uM", half_saturation_uM, positive=True, scalar=True)
        concentration = _value("intracellular concentration", stock / volume / MOLECULES_PER_UM_UM3)
        scale = np.maximum(concentration, half)
        mu = maximum * (concentration / scale) / (concentration / scale + half / scale)
    elif policy == "simplified_yield":
        if half_saturation_uM is not None:
            raise ValueError("simplified_yield has no Monod half-saturation parameter")
        mu = np.broadcast_to(maximum, stock.shape)
    else:
        raise ValueError("unknown growth policy")
    if dt == 0:
        return GrowthReadout(stock.copy(), volume.copy(), np.zeros_like(stock), np.zeros_like(stock))
    need = _value("growth request", _product("volume growth request", mu, volume, dt) / yield_v)
    used = np.minimum(stock, need)
    increment = _product("accepted volume growth", used, yield_v)
    new_volume = _value("new volume", volume + increment)
    actual = _value("actual growth rate", increment / volume / dt)
    return GrowthReadout(stock - used, new_volume, used, actual)


def advance_copy_number(copies, synthesis_copies_min, turnover_per_min, dt_s):
    """Exact frozen synthesis/turnover; volume dilution never subtracts copies."""
    count, synthesis = _align(_value("copies", copies), _value("synthesis_copies_min", synthesis_copies_min))
    rate = _value("turnover_per_min", turnover_per_min, scalar=True)
    dt = _value("dt_s", dt_s, scalar=True) / 60
    if rate == 0:
        return _value("new copies", count + _product("copy synthesis", synthesis, dt))
    with np.errstate(over="ignore", under="ignore"):
        x = rate * dt
        fraction = -np.expm1(-x)
        decay = np.exp(-x)
    # dt * exprel(-x) avoids synthesis/rate overflowing when turnover is tiny.
    factor = dt if x == 0 else fraction / rate
    return _value("new copies", count * decay + _product("copy synthesis", synthesis, factor))


def rebuilt_expression_rate(health, actual_growth_per_min, inp_occupancy, *, expression_copies_min,
                            health_floor, health_hill, metabolic_floor, metabolic_half_growth_per_min,
                            burden_half_fraction, burden_hill):
    health, mu, phi = _align(_value("health", health, upper=1),
                             _value("actual_growth_per_min", actual_growth_per_min),
                             _value("inp_occupancy", inp_occupancy))
    base = _value("expression_copies_min", expression_copies_min, scalar=True)
    hf = _value("health_floor", health_floor, upper=1, scalar=True)
    hh = _value("health_hill", health_hill, positive=True, scalar=True)
    mf = _value("metabolic_floor", metabolic_floor, upper=1, scalar=True)
    mh = _value("metabolic_half_growth_per_min", metabolic_half_growth_per_min, positive=True, scalar=True)
    health_gate = hf + (1 - hf) * health**hh
    scale = np.maximum(mu, mh)
    metabolic_gate = mf + (1 - mf) * (mu / scale) / (mu / scale + mh / scale)
    burden_gate = 1 - motor_bias(phi, half_uM=burden_half_fraction, hill=burden_hill)
    return _product("INP synthesis", base, health_gate, metabolic_gate, burden_gate)


@dataclass(frozen=True)
class HealthReadout:
    health: np.ndarray
    death_hazard_per_min: np.ndarray


def advance_simplified_health(health, actual_growth_per_min, burden, dt_s, *, repair_per_min,
                              burden_per_min, starvation_per_min, max_growth_per_min,
                              death_max_per_min, death_threshold):
    h, mu, phi = _align(_value("health", health, upper=1),
                        _value("actual_growth_per_min", actual_growth_per_min), _value("burden", burden))
    dt = _value("dt_s", dt_s, scalar=True) / 60
    repair = _value("repair_per_min", repair_per_min, scalar=True)
    toxicity = _value("burden_per_min", burden_per_min, scalar=True)
    starvation = _value("starvation_per_min", starvation_per_min, scalar=True)
    maximum = _value("max_growth_per_min", max_growth_per_min, positive=True, scalar=True)
    death = _value("death_max_per_min", death_max_per_min, scalar=True)
    threshold = _value("death_threshold", death_threshold, positive=True, upper=1, scalar=True)
    derivative = repair * (1 - h) - _product("health burden", toxicity, phi) - starvation * np.maximum(0, 1 - mu / maximum)
    change = derivative * dt
    if not np.all(np.isfinite(change)):
        raise ValueError("health increment is not representable")
    new = np.clip(h + change, 0, 1)
    return HealthReadout(new, death * np.maximum(0, 1 - new / threshold))


@dataclass(frozen=True)
class RebuiltHealthParameters:
    repair_max_per_min: float
    repair_half_growth_per_min: float
    tolerated_effective_occupancy: float
    occupancy_half_excess: float
    occupancy_hill: float
    occupancy_toxicity_max_per_min: float
    excess_toxicity_max_per_min: float
    excess_half_copies: float
    starvation_max_per_min: float
    starvation_half_growth_per_min: float
    starvation_hill: float
    death_half_health: float
    death_hazard_max_per_min: float
    death_hill: float
    inp_toxicity_weight: float

    def __post_init__(self):
        for field in fields(self):
            _value(field.name, getattr(self, field.name), scalar=True,
                   positive=field.name in {"repair_half_growth_per_min", "occupancy_half_excess",
                     "occupancy_hill", "excess_half_copies", "starvation_half_growth_per_min",
                     "starvation_hill", "death_half_health", "death_hill"})


def advance_rebuilt_health(health, actual_growth_per_min, phi_pts, phi_inp, excess_copies,
                            dt_s, p: RebuiltHealthParameters):
    if not isinstance(p, RebuiltHealthParameters):
        raise ValueError("rebuilt health requires RebuiltHealthParameters")
    h, mu, pts, inp, excess = _align(_value("health", health, upper=1),
        _value("actual_growth_per_min", actual_growth_per_min), _value("phi_pts", phi_pts, upper=1),
        _value("phi_inp", phi_inp, upper=1), _value("excess_copies", excess_copies))
    dt = _value("dt_s", dt_s, scalar=True) / 60
    repair = p.repair_max_per_min * motor_bias(mu, half_uM=p.repair_half_growth_per_min, hill=1) * (1 - h)
    effective = _value("effective occupancy", pts + _product("INP burden", p.inp_toxicity_weight, inp))
    over = np.maximum(effective - p.tolerated_effective_occupancy, 0)
    toxicity = (p.occupancy_toxicity_max_per_min * motor_bias(over, half_uM=p.occupancy_half_excess, hill=p.occupancy_hill)
                + p.excess_toxicity_max_per_min * motor_bias(excess, half_uM=p.excess_half_copies, hill=1))
    starve = p.starvation_max_per_min * (1 - motor_bias(mu, half_uM=p.starvation_half_growth_per_min, hill=p.starvation_hill))
    change = (repair - toxicity - starve) * dt
    if not np.all(np.isfinite(change)):
        raise ValueError("health increment is not representable")
    new = np.clip(h + change, 0, 1)
    hazard = p.death_hazard_max_per_min * (1 - motor_bias(new, half_uM=p.death_half_health, hill=p.death_hill))
    return HealthReadout(new, hazard)


def surface_adder_delta(*, reference_birth_volume_um3, target_volume_um3, radius_um,
                        cv, normal_deviate, minimum_area_um2):
    birth = capsule_geometry_from_volume(reference_birth_volume_um3, radius_um).area_um2
    target = capsule_geometry_from_volume(target_volume_um3, radius_um).area_um2
    cv = _value("cv", cv, scalar=True)
    floor = _value("minimum_area_um2", minimum_area_um2, positive=True, scalar=True)
    from .chemotaxis import _signed
    draw = _signed("normal_deviate", normal_deviate)
    delta = np.maximum(target - birth, floor)
    return _value("surface-adder increment", np.maximum(floor, np.abs(delta * (1 + cv * draw))))


def division_split_bounds(volume_um3, radius_um):
    volume = _value("volume_um3", volume_um3, positive=True)
    radius = _value("radius_um", radius_um, positive=True, scalar=True)
    minimum = _product("minimum sphere volume", 4 * np.pi / 3, radius, radius, radius)
    if np.any(volume < 2 * minimum):
        raise ValueError("mother cannot form two fixed-radius capsules")
    return np.maximum(.35, minimum / volume), np.minimum(.65, 1 - minimum / volume)


@dataclass(frozen=True)
class DivisionReadout:
    first_volume_um3: np.ndarray
    second_volume_um3: np.ndarray
    first_substrate_molecules: np.ndarray
    second_substrate_molecules: np.ndarray
    first_inp_copies: np.ndarray
    second_inp_copies: np.ndarray
    extra_pole_area_um2: np.ndarray


def division_split(volume_um3, intracellular_molecules, inp_copies, split_fraction, *, radius_um):
    v, n, copies, fraction = _align(_value("volume_um3", volume_um3, positive=True),
        _value("intracellular_molecules", intracellular_molecules), _value("inp_copies", inp_copies),
        _value("split_fraction", split_fraction, positive=True, upper=1))
    low, high = division_split_bounds(v, radius_um)
    if np.any(fraction < low) or np.any(fraction > high):
        raise ValueError("split fraction is outside source/geometry bounds")
    v1, n1, c1 = v * fraction, n * fraction, copies * fraction
    v2, n2, c2 = v - v1, n - n1, copies - c1
    capsule_geometry_from_volume(v1, radius_um)
    capsule_geometry_from_volume(v2, radius_um)
    extra = np.full_like(v, _product("new pole area", 4 * np.pi / 3, radius_um, radius_um))
    return DivisionReadout(v1, v2, n1, n2, c1, c2, extra)

# ---- 原 science/survival.py ----

def advance_reserve(reserve, accepted, dt_s, maintenance_molecules_s, correction=None):
    reserve, accepted = _align(_value('reserve', reserve), _value('accepted', accepted))
    dt = _value('dt_s', dt_s, scalar=True)
    rate = _value('maintenance_molecules_s', maintenance_molecules_s, scalar=True)
    demand = _product('maintenance demand', rate, dt)
    from .chemotaxis import _signed
    low = np.zeros_like(reserve) if correction is None else _signed('reserve correction', correction)
    reserve, accepted, low = _align(reserve, accepted, low)
    after, used, next_low = np.empty_like(reserve), np.empty_like(reserve), np.empty_like(reserve)
    # A two-component amount retains arrivals below the current reserve ULP.
    # Exact binary rationals determine consumption before rounding either part;
    # this changes no biological rates and avoids silently dropping tiny flux.
    for index in np.ndindex(reserve.shape):
        total = Fraction(float(reserve[index])) + Fraction(float(low[index])) + Fraction(float(accepted[index]))
        if total < 0:
            raise ValueError('Corrected reserve must be nonnegative')
        consumed_exact = min(total, Fraction(float(demand)))
        consumed = float(consumed_exact)
        if Fraction(consumed) > consumed_exact:
            consumed = math.nextafter(consumed, 0.)
        remainder = total - Fraction(consumed)
        high = float(remainder)
        if not math.isfinite(high):
            raise ValueError('Reserve overflows')
        tail = float(remainder - Fraction(high))
        residual = abs(remainder - Fraction(high) - Fraction(tail))
        bound = 1e-10 * float(accepted[index]) + 8 * math.ulp(float(accepted[index]))
        if residual > Fraction(bound):
            raise ValueError('Reserve compensation exceeds the transfer precision budget')
        after[index], used[index], next_low[index] = high, consumed, tail
    unmet = np.zeros_like(after) if rate == 0 else np.maximum(0., dt - used / rate)
    return ReserveReadout(after, used, unmet, next_low)

# ---- 原 science/chemotaxis.py ----

def tumble_hazard(bias, *, minimum_s, maximum_s):
    b = _value("bias", bias, upper=1)
    lo = _value("minimum_s", minimum_s, scalar=True)
    hi = _value("maximum_s", maximum_s, scalar=True)
    if hi < lo:
        raise ValueError("maximum_s must be >= minimum_s")
    return lo + (hi - lo) * b
