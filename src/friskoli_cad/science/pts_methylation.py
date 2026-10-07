"""PTS-driven CheA activity with CheR/CheB methylation adaptation.

    sugar uptake flux -> EI phosphorylation state -> CheA activity
                                                          ^
                                       CheR/CheB methylation (adaptation)

The PTS does not ligate the methyl-accepting chemotaxis proteins. Uptake flux
sets the phosphorylation state of enzyme I, and dephosphorylated EI inhibits
CheA in the sensory complex (Neumann et al. 2012; Somavanshi et al. 2016).
Methylation opposes that shift and supplies the adaptation (Barkai & Leibler
1997). Both couplings are linear: the only amplification in the chain is the
flagellar motor, whose Hill coefficient is a motor property (Cluzel et al.
2000), not a PTS property.

Nothing here is a free gain. ``activity_coupling`` fixes the two slopes from the
declared ranges of the two inputs, and the adapted state is derived from the
ambient concentration by the caller.
"""
from dataclasses import dataclass, fields
import numpy as np
from .pts import _align, _value, _rates, _relax, _product
from .chemotaxis import CheYParameters, advance_chey_from_activity, motor_bias


@dataclass(frozen=True)
class PTSMethylationParameters:
    ei_dephos_per_molecule: float
    ei_rephos_s: float
    methylation_min: float
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
                   positive=f.name in {'methylation_max', 'baseline_activity',
                                       'chea_total_uM', 'chey_total_uM',
                                       'motor_hill', 'motor_half_uM'})
        if not 0. < self.baseline_activity < 1.:
            raise ValueError('baseline_activity must lie strictly between zero and one')
        if self.methylation_max <= self.methylation_min:
            raise ValueError('methylation_max must exceed methylation_min')


def activity_coupling(p):
    """Sensitivities ``(da/de, da/dm)`` of the sensory complex.

    A full PTS swing is ``e: 0 -> 1``, because ``e`` is a fraction by
    construction. A full methylation swing is ``methylation_min ->
    methylation_max``. Each input is declared to span the whole activity range
    ``[0, 1]``; that is exactly what lets methylation undo any PTS drive and
    restore the baseline, so neither slope carries a free gain.
    """
    if not isinstance(p, PTSMethylationParameters):
        raise ValueError('activity_coupling requires PTSMethylationParameters')
    return 1., 1. / (p.methylation_max - p.methylation_min)


def complex_activity(ei_fraction, methylation, p):
    """CheA activity: the PTS drive inhibits it, CheR methylation activates it.

    Uptake flux dephosphorylates EI, and dephosphorylated EI inhibits CheA, so
    the PTS term carries a minus sign. Methyl groups added by CheR favour the
    active state of the sensory complex, so the methylation term carries a plus
    sign. That opposition is what lets methylation undo any PTS drive.
    """
    if not isinstance(p, PTSMethylationParameters):
        raise ValueError('complex_activity requires PTSMethylationParameters')
    gain_e, gain_m = activity_coupling(p)
    e, m = _align(_value('ei_fraction', ei_fraction, upper=1),
                  _value('methylation', methylation, upper=p.methylation_max))
    activity = p.baseline_activity - gain_e * e + gain_m * (m - p.methylation_min)
    if not np.isfinite(activity).all():
        raise ValueError('Complex activity must be finite')
    return np.clip(activity, 0., 1.)


def adapted_methylation(ei_fraction, p):
    """Methylation at which the complex returns to its baseline activity."""
    gain_e, gain_m = activity_coupling(p)
    return p.methylation_min + gain_e / gain_m * _value('ei_fraction', ei_fraction, upper=1)


def methylation_rate(tau_methylation_s, methylation_min, methylation_max):
    """Rate constant that gives the declared methylation time constant.

    Methylation relaxes with eigenvalue ``adaptation_rate * gain_m``, and
    ``gain_m`` is the reciprocal of the methylation span, so the raw rate is
    ``span / tau`` rather than ``1 / tau``. Declaring the time constant and
    deriving the rate keeps the two from drifting apart.
    """
    tau = _value('tau_methylation_s', tau_methylation_s, positive=True, scalar=True)
    span = _value('methylation_max', methylation_max, positive=True, scalar=True) \
        - _value('methylation_min', methylation_min, scalar=True)
    if span <= 0:
        raise ValueError('methylation_max must exceed methylation_min')
    return span / tau


def ei_dephos_per_molecule(ei_rephos_s, functional_copies, turnover_s):
    """EI dephosphorylation per molecule taken up, from one stated anchor.

    EI is declared half-dephosphorylated exactly where PTS uptake is
    half-saturated, that is at ``functional_copies * turnover_s / 2`` molecules
    per second. That pins the sensor working point to the transport working
    point instead of leaving a free rate constant behind.
    """
    rate = _product('PTS half-saturation flux',
                    _value('functional_copies', functional_copies, positive=True),
                    _value('turnover_s', turnover_s, positive=True)) / 2.
    return _value('ei_rephos_s', ei_rephos_s) / rate


def chey_rates(tau_chey_s, chey_total_uM, baseline_activity, chea_total_uM):
    """CheY phosphorylation and dephosphorylation rates for a balanced cycle.

    A steady state requires the two fluxes to be equal. Declaring that balance
    to hold at the baseline activity makes half of CheY phosphorylated there,
    and the response time is then ``1 / (2 * k_dephos)``.
    """
    tau = _value('tau_chey_s', tau_chey_s, positive=True, scalar=True)
    _value('chey_total_uM', chey_total_uM, positive=True, scalar=True)
    chea = _value('chea_total_uM', chea_total_uM, positive=True, scalar=True)
    a0 = _value('baseline_activity', baseline_activity, positive=True, scalar=True)
    if a0 >= 1:
        raise ValueError('baseline_activity must lie strictly between zero and one')
    dephos = 1. / (2. * tau)
    return dephos / (a0 * chea), dephos


def self_consistent_motor_half(baseline_chey_p_um, baseline_activity, motor_hill):
    """Motor half-point that makes the declared baseline activity the actual one.

    Criterion 0: the motor readout must report ``baseline_activity`` at the
    baseline CheY-P, so the half-point is derived rather than chosen.
    """
    if not 0. < baseline_activity < 1.:
        raise ValueError('baseline activity must lie strictly between zero and one')
    return (baseline_chey_p_um
            * ((1. - baseline_activity) / baseline_activity) ** (1. / motor_hill))


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
        lower = np.maximum(p.methylation_min, m - amount * (1 - p.baseline_activity))
        upper = np.minimum(p.methylation_max, m + amount * p.baseline_activity)
        for _ in range(48):
            mid = lower / 2 + upper / 2
            residual = mid - np.clip(m + amount * (p.baseline_activity - complex_activity(e, mid, p)),
                                     p.methylation_min, p.methylation_max)
            lower = np.where(residual < 0, mid, lower)
            upper = np.where(residual > 0, mid, upper)
            lower = np.where(residual == 0, mid, lower)
            upper = np.where(residual == 0, mid, upper)
        m = lower / 2 + upper / 2
    activity = complex_activity(e, m, p)
    cp = CheYParameters(**{f.name: getattr(p, f.name) for f in fields(CheYParameters)})
    y = advance_chey_from_activity(activity, y, dt, cp)
    return e, m.copy(), activity, y, motor_bias(y, half_uM=p.motor_half_uM, hill=p.motor_hill)
