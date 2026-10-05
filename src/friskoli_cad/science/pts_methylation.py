"""PTS receptor adaptation with bounded methylation and first-order splitting."""
from dataclasses import dataclass, fields
import numpy as np
from .pts import _align, _value, _rates, _relax, _product
from .chemotaxis import CheYParameters, advance_chey_from_activity, motor_bias


@dataclass(frozen=True)
class PTSMethylationParameters:
    ei_dephos_per_molecule: float
    ei_rephos_s: float
    pts_energy_gain: float
    methylation_energy: float
    methylation_reference: float
    methylation_max: float
    adaptation_rate_s: float
    baseline_activity: float
    chea_total_uM: float
    chey_total_uM: float
    chey_phos_per_uM_s: float
    chey_dephos_s: float
    motor_hill: float
    motor_half_uM: float

    def __post_init__(self):
        for f in fields(self):
            _value(f.name, getattr(self, f.name), scalar=True,
                   positive=f.name in {'methylation_energy', 'methylation_max',
                                      'baseline_activity', 'motor_hill', 'motor_half_uM'})
        if self.baseline_activity >= 1 or self.methylation_reference > self.methylation_max:
            raise ValueError('Invalid activity baseline or methylation reference')


def receptor_activity(e, m, p):
    energy = (np.log1p(-p.baseline_activity) - np.log(p.baseline_activity)
              + _product('PTS energy', p.pts_energy_gain, e)
              - _product('methylation energy', p.methylation_energy, m - p.methylation_reference))
    if not np.isfinite(energy).all():
        raise ValueError('Receptor energy must be finite')
    tail = np.exp(-np.abs(energy))
    return np.where(energy >= 0, tail / (1 + tail), 1 / (1 + tail))


def advance(accepted_flux, ei_fraction, methylation, chey_p, dt_s, p):
    """Return EI, methylation, activity, CheY-P and motor bias, without mutation."""
    if not isinstance(p, PTSMethylationParameters):
        raise ValueError('PTS methylation requires PTSMethylationParameters')
    dt = _value('dt_s', dt_s, scalar=True)
    flux, e, m, y = _align(_value('accepted_flux', accepted_flux),
        _value('ei_fraction', ei_fraction, upper=1),
        _value('methylation', methylation, upper=p.methylation_max),
        _value('chey_p', chey_p, upper=p.chey_total_uM))
    target, scale, denominator = _rates(_product('EI rate', flux, p.ei_dephos_per_molecule), p.ei_rephos_s)
    e = _relax(e, target, scale, denominator, dt)
    amount = _product('adaptation increment', p.adaptation_rate_s, dt)
    if amount:
        # Bounded backward Euler for methylation feedback.
        lower = np.maximum(0., m - amount * (1 - p.baseline_activity))
        upper = np.minimum(p.methylation_max, m + amount * p.baseline_activity)
        for _ in range(48):
            mid = lower / 2 + upper / 2
            residual = mid - np.clip(m + amount * (p.baseline_activity - receptor_activity(e, mid, p)), 0., p.methylation_max)
            lower = np.where(residual < 0, mid, lower)
            upper = np.where(residual > 0, mid, upper)
            lower = np.where(residual == 0, mid, lower)
            upper = np.where(residual == 0, mid, upper)
        m = lower / 2 + upper / 2
    activity = receptor_activity(e, m, p)
    cp = CheYParameters(**{f.name: getattr(p, f.name) for f in fields(CheYParameters)})
    y = advance_chey_from_activity(activity, y, dt, cp)
    return e, m.copy(), activity, y, motor_bias(y, half_uM=p.motor_half_uM, hill=p.motor_hill)
