"""Conservative, paired-seed exploration; never an experimental efficacy claim."""
import statistics

from friskoli_cad.design_delivery import _validate, _report, _finite


def _stats(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'sample_sd': statistics.stdev(values) if len(values) >= 2 else None,
            'minimum': min(values) if values else None, 'maximum': max(values) if values else None}


def _add(reasons, reason):
    if reason not in reasons:
        reasons.append(reason)


def _evaluate(data):
    design = data['design']
    brief, candidates = design['brief'], design['candidates']
    policy = brief.get('selection_policy') if brief['brief_version'] in ('0.2.0', '0.3.0') else None
    result_constraints = brief.get('result_constraints', []) if brief['brief_version'] in ('0.2.0', '0.3.0') else []
    goal, planned = brief['goal'], set(brief['seeds'])
    _, records = _report(data)
    evaluated, evidence, all_series = [], {}, set()
    global_reasons = []
    if policy is None:
        global_reasons.append('selection_policy_not_configured')
    minimum = policy['min_repeats'] if policy else 2
    controls = [c for c in candidates if c['kind'] == 'control']
    if len(controls) != 1:
        global_reasons.append('unique_control_required')
    if len(candidates) - len(controls) < 2:
        global_reasons.append('insufficient_candidate_count')
    if len(planned) < minimum:
        global_reasons.append('insufficient_repeats')
    for candidate in candidates:
        cid = candidate['id']
        pairs = [(row, run) for row, run in zip(records, data['runs'], strict=True)
                 if row['candidate_id'] == cid and (run.get('design_ref') or {}).get('design_id') == design['id']]
        reasons, valid, excluded_attempts = [], [], []
        successful_seeds = {row['seed'] for row, _ in pairs if row['reason'] is None}
        for row, run in pairs:
            reason = row['reason']
            if row.get('duplicate_seed'):
                _add(reasons, 'duplicate_seed')
            if reason:
                superseded = (reason == 'failed_partial_or_incomplete'
                              and run.get('status') in ('failed', 'cancelled', 'interrupted', 'rejected')
                              and row['seed'] in successful_seeds)
                excluded_attempts.append({'run_id':row['run_id'], 'seed':row['seed'],
                    'reason':reason, 'status':run.get('status'), 'superseded_by_complete_repeat':superseded})
                if not superseded:
                    _add(reasons, reason)
            else:
                valid.append((row, run))
        seeds = {row['seed'] for row, _ in valid}
        series = {row['series'] for row, _ in valid}
        all_series.update(series)
        if seeds != planned:
            _add(reasons, 'missing_seed')
        if len(series) > 1:
            _add(reasons, 'incompatible_series')
        if len(seeds) < minimum:
            _add(reasons, 'insufficient_repeats')
        evidence[cid] = {row['seed']: row['value'] for row, _ in valid}
        values = [row['value'] for row, _ in valid]
        objective = _stats(values)
        constraints = []
        for constraint in result_constraints:
            vals = [run['replay']['snapshots'][-1]['metrics']['by_group'].get(constraint['group_id'], {}).get(constraint['metric'])
                    for _, run in valid]
            finite = [v for v in vals if _finite(v)]
            known = not reasons and len(finite) == len(planned)
            passed = known and all(v <= constraint['value'] if constraint['operator'] == '<=' else v >= constraint['value'] for v in finite)
            constraints.append({**constraint, 'status': ('pass' if passed else 'fail') if known else 'unknown',
                                'minimum': min(finite) if finite else None, 'maximum': max(finite) if finite else None})
        incomplete = bool(reasons) or any(c['status'] == 'unknown' for c in constraints)
        if any(c['status'] == 'unknown' for c in constraints):
            _add(reasons, 'result_constraint_unknown')
        hard_failed = any(c['kind'] == 'hard' and c['status'] == 'fail' for c in constraints)
        if hard_failed and candidate['kind'] != 'control':
            _add(reasons, 'hard_result_constraint_failed')
        evaluated.append({'candidate_id': cid, 'name': candidate['name'], 'kind': candidate['kind'],
                          'status': 'incomplete' if incomplete else ('excluded' if hard_failed and candidate['kind'] != 'control' else 'eligible'),
                          'reasons': reasons, 'objective': objective, 'constraints': constraints,
                          'control_comparison': None, 'seeds': sorted(seeds), 'excluded_attempts':excluded_attempts})
    if len(all_series) > 1:
        _add(global_reasons, 'incompatible_series')
        for item in evaluated:
            _add(item['reasons'], 'incompatible_series')
            item['status'] = 'incomplete'
    known_ids = {c['id'] for c in candidates}
    if any((r.get('design_ref') or {}).get('design_id') == design['id']
           and (r.get('design_ref') or {}).get('candidate_id') not in known_ids for r in data['runs']):
        _add(global_reasons, 'unknown_candidate_evidence')
    control = next((c for c in evaluated if c['kind'] == 'control'), None) if len(controls) == 1 else None
    control_ready = control is not None and control['status'] == 'eligible'
    for item in evaluated:
        if item['kind'] == 'control' or item['status'] == 'incomplete':
            continue
        if not control_ready:
            _add(item['reasons'], 'control_unavailable')
            item['status'] = 'incomplete'
            continue
        direction = 1 if goal['direction'] == 'maximize' else -1
        differences = [direction * (evidence[item['candidate_id']][seed] - evidence[control['candidate_id']][seed])
                       for seed in sorted(planned)]
        if any(not _finite(d) for d in differences):
            _add(item['reasons'], 'nonfinite_control_comparison')
            item['status'] = 'incomplete'
            continue
        item['control_comparison'] = _stats(differences)
        if policy and not all(d > policy['min_control_improvement'] for d in differences):
            _add(item['reasons'], 'control_improvement_not_met')
            item['status'] = 'excluded'
    if any(c['status'] == 'incomplete' for c in evaluated):
        _add(global_reasons, 'incomplete_candidate_evidence')
    eligible = [c for c in evaluated if c['kind'] == 'candidate' and c['status'] == 'eligible']
    recommended = None
    if not global_reasons:
        if not eligible:
            global_reasons.append('no_eligible_candidate')
        else:
            best = (max if goal['direction'] == 'maximize' else min)(c['objective']['mean'] for c in eligible)
            winners = [c for c in eligible if c['objective']['mean'] == best]
            if len(winners) == 1:
                recommended = winners[0]['candidate_id']
            else:
                global_reasons.append('tied_objective')
    return {'evaluation_version': '0.1.0', 'design_id': design['id'],
            'status': 'recommended' if recommended else 'no_recommendation',
            'recommended_candidate_id': recommended, 'reasons': global_reasons, 'candidates': evaluated,
            'limitations': [
                'Exploratory simulation only; parameters are not experimentally calibrated.',
                'Paired seed differences and sample SD are descriptive, not a significance test or confidence interval.',
                'Every planned seed must be complete and every paired improvement must exceed the declared threshold.',
                'Soft constraints are reported individually; quantities with different units are never summed into a score.']}


def evaluate_design(payload):
    """Evaluate preserved evidence without executing it or consulting the installed registry."""
    return _evaluate(_validate(payload))
