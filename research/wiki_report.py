"""Collect completed Wiki studies into a traceable bilingual figure index."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np


def rows(path):
    with Path(path).open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def paired_endpoint_summary(records, duration, bootstrap):
    """Keep seed pairing explicit when summarizing the final observed layer."""
    endpoint = [r for r in records if abs(float(r['time_s']) - duration) < 1e-9]
    result = {}
    metrics = ('arrival_fraction', 'contact_s_per_cell', 'converted_per_cell', 'region_fraction',
               'enrichment_vs_uniform_fluid', 'contact_entries', 'chey_p_mean_uM')
    for mechanism in sorted({r['mechanism'] for r in endpoint}):
        group = [r for r in endpoint if r['mechanism'] == mechanism]
        paired = {}
        for feedback in ('True', 'False'):
            selected = [r for r in group if r['feedback'] == feedback]
            paired[feedback] = {int(r['seed']): r for r in selected}
            if len(paired[feedback]) != len(selected):
                raise ValueError('Endpoint records contain a duplicate seed')
        seeds = sorted(paired['True'])
        if not seeds or set(seeds) != set(paired['False']):
            raise ValueError('Endpoint comparisons need matching feedback and control seeds')
        summary = {}
        for metric in metrics:
            if not all(metric in r for r in group):
                continue
            values = {key: np.array([float(records_by_seed[s][metric]) for s in seeds])
                      for key, records_by_seed in paired.items()}
            if any(not np.isfinite(v).all() for v in values.values()):
                raise ValueError('Endpoint values must be finite')
            difference = values['True'] - values['False']
            interval = [None, None]
            if len(seeds) > 1:
                rng = np.random.default_rng(bootstrap['seed'])
                means = difference[rng.integers(len(seeds), size=(bootstrap['samples'], len(seeds)))].mean(axis=1)
                interval = np.quantile(means, bootstrap['interval']).tolist()
            summary[metric] = {
                **{key: {'mean': float(v.mean()), 'sd': float(v.std(ddof=1)) if len(v) > 1 else None}
                   for key, v in values.items()},
                'paired_difference': [float(difference.mean()), *interval],
                'n_positive': int(np.sum(difference > 1e-10)),
                'n_negative': int(np.sum(difference < -1e-10)),
                'paired_values': difference.tolist(),
            }
        result[mechanism] = {'seeds': seeds, 'metrics': summary}
    return {'duration_s': duration, 'bootstrap': bootstrap, 'endpoint_by_mechanism': result}


def build_report(signals, spatial, destination):
    signals, spatial, destination = Path(signals), Path(spatial), Path(destination)
    signal_manifest = json.loads((signals / 'manifest.json').read_text(encoding='utf-8'))
    spatial_manifest = json.loads((spatial / 'manifest.json').read_text(encoding='utf-8'))
    if signal_manifest['source_hashes'] != spatial_manifest['source_hashes']:
        raise ValueError('Signal and spatial studies must use matching CAD source hashes')
    for directory, manifest in ((signals, signal_manifest), (spatial, spatial_manifest)):
        snapshot = directory / 'runner.py'
        if snapshot.exists() and hashlib.sha256(snapshot.read_bytes()).hexdigest() != manifest['script_sha256']:
            raise ValueError('A runner snapshot differs from the recorded study script')
    audits = rows(spatial / 'conservation-and-call-audit.csv')
    if not audits or any(r['audit_passed'] != 'True' or r['signal_advance_calls'] != r['expected_signal_calls'] for r in audits):
        raise ValueError('Complete and inspect all spatial audits before making the report')
    destination.mkdir(parents=True, exist_ok=True)
    def href(path):
        return os.path.relpath(path, destination).replace('\\', '/')
    figures = []
    def figure(identifier, directory, filename, zh, en, *, seeds=0, cells=1, samples=0):
        path = directory / (filename + '.svg')
        if not path.exists():
            return
        source = ('扫描与其余数值为假设；固定马达 Hill 系数取自文献。'
                  if directory == signals else '扫描变量与空间设置为假设；固定马达 Hill 系数取自文献。')
        counts = f'运动 seed 数={seeds}；每次细胞数={cells}；参数抽样数={samples}。'
        figures.append({'id': identifier, 'svg': href(path), 'png': href(directory / (filename + '.png')),
                        'parameter_sources': {'varied_parameters': 'assumption', 'motor_hill': 'literature', 'measured': []},
                        'n_motility_seeds': seeds, 'cells_per_run': cells, 'n_parameter_samples': samples,
                        'caption_zh': zh + ' 参数来源：' + source + ' ' + counts,
                        'caption_en': en + f' Varied settings are assumed; the fixed motor Hill coefficient is literature-derived. '
                                      f'Motility seeds: {seeds}; cells per run: {cells}; parameter samples: {samples}.'})
    figure('A / M06 / M09', signals, 'A-dose-response',
           '维持外部糖浓度的稳态信号链。逐层显示摄取、去磷酸化 EI、CheA、CheY-P、motor bias 和独立计算的稳态导数。'
           '关闭适应时固定同一参考态的甲基化；其余参数相同。',
           'Steady-state signal layers under maintained concentrations. Adaptation-off holds the same reference methylation fixed; other parameters are shared.')
    figure('B / M08', signals, 'B-step-response',
           f'相同浓度阶跃下的下游适应比较。5 s 时由 0.1 升至 1 µM，35 s 回落，观察至 65 s，时间步 {signal_manifest["dt_s"]:g} s。'
           '两组共用输入及初始态，仅适应速率改变。',
           f'Downstream adaptation under identical concentration inputs: 0.1→1 µM at 5 s and back at 35 s, observed to 65 s with {signal_manifest["dt_s"]:g} s steps. Only adaptation rate changes.')
    samples = signal_manifest['C']['n_parameter_samples']
    figure('C / M15', signals, 'C-window-uncertainty',
           f'不同适应背景下的 10% 浓度阶跃，在 20 s 内记录峰值响应与瞬态增益。中线与图带分别为参数响应的中位数及 5%–95% 分位数；'
           f'对数 LHS 算法 seed={signal_manifest["C"]["parameter_seed"]}。各参数样本在对应背景初始化。',
           f'Peak responses to 10% steps within 20 s across adapted backgrounds. Lines and bands show parameter medians and 5–95% quantiles; '
           f'log-LHS seed {signal_manifest["C"]["parameter_seed"]}. Each sample starts adapted to its background.', samples=samples)
    seeds = len(spatial_manifest['motility_seeds'])
    screen_seeds = min(4, seeds)
    duration = spatial_manifest['duration_s']
    figure('M01', spatial, 'M01-matched-trajectories',
           f'预先指定 seed 1 的 XY 轨迹投影，模拟 {duration:g} s。反馈与固定 bias 对照的源强、几何、边界及初始菌群相同；中心方块表示材料投影。',
           f'XY trajectory projection for preselected seed 1 over {duration:g} s. Feedback and fixed-bias controls share source, geometry, boundaries and starting cells.', seeds=1, cells=8)
    figure('M02', spatial, 'M02-motion-scan',
           f'固定 motor bias 下扫描游动速度及转向频率，模拟 {duration:g} s。点与误差线为独立运行均值及样本标准差。'
           'MSD/(6T) 是当前时间窗内的描述量，径向指数定义为朝材料中心的进展除以实际路径长度。',
           f'Speed and tumble-rate scan at fixed motor bias over {duration:g} s. Means and sample SD are shown. MSD/(6T) describes this finite window; '
           'the radial index is progress toward the center divided by path length.', seeds=screen_seeds, cells=8)
    for identifier, filename, detail in [('M03', 'M03-field-snapshots', 'XY 浓度切面'), ('M03-profile', 'M03-field-profiles', '浓度剖面线')]:
        figure(identifier, spatial, filename,
               f'相同 seed 1、扩散和摄取设置下改变释放因子的{detail}，初始溶解糖为零，模拟 {duration:g} s。源位置、材料几何、边界及菌群相同。',
               f'Concentration sections with release factors varied at seed 1 and shared diffusion/uptake, initially zero dissolved sugar, t={duration:g} s. '
               'Source location, material geometry, boundaries and cells are shared.', seeds=1, cells=8)
    figure('M04', spatial, 'M04-gradient-phase',
           f'零初始溶解糖下释放×扩散×摄取的有限时程扫描，模拟 {duration:g} s。色值为达到梯度标准的平均累计时长；'
           '探测距表面 2–8 µm，要求 C≥0.01 µM、|∇C|/C≥0.02 µm⁻¹，合格体素占比≥10%。改变扫描变量，其余几何、边界与初始条件保持相同。',
           f'Finite-horizon release×diffusion×uptake scan from zero dissolved sugar, T={duration:g} s. Colors show mean time meeting the criterion: '
           'C≥0.01 µM and |∇C|/C≥0.02 µm⁻¹ in at least 10% of probe voxels 2–8 µm from the surface. Other geometry, boundaries and initial conditions are shared.',
           seeds=screen_seeds, cells=8)
    for identifier, filename, zh, en in [
        ('M12', 'M12-OAT', '五个参数因子各改变 ±20% 的归一化 OAT 排序。', 'Normalized OAT screen with ±20% factor changes.'),
        ('M13', 'M13-parameter-plane', '适应速率×EI 去磷酸化系数的平均转化量热图。', 'Mean conversion across adaptation-rate and EI-dephosphorylation factors.'),
        ('M14', 'M14-Morris', f'四水平、{spatial_manifest["Morris"]["trajectories"]} 条参数轨迹的 Morris µ* 与 σ；以对数因子坐标计算，输出为每个初始细胞的转化量。',
         f'Four-level Morris µ* and σ on log-factor coordinates; {spatial_manifest["Morris"]["trajectories"]} parameter trajectories, conversion per founder as the output.')]:
        figure(identifier, spatial, filename,
               zh + f'各配置模拟 {duration:g} s，共用同一组运动 seed、源位置、几何、边界及初始菌群；扫描范围见 manifest。',
               en + f' Each configuration lasts {duration:g} s and shares motility seeds, source location, geometry, boundaries and starting cells; ranges are in the manifest.',
               seeds=screen_seeds, cells=8)
    figure('M16', spatial, 'M16-layered-evaluation',
           f'趋化反馈与固定 bias 对照的分层评价，模拟 {duration:g} s。分别展示相对梯度、CheY-P、到达、接触、转化与区域占比；'
           '线与阴影为运行均值和样本标准差。相同 seed 下源强、几何、边界及初始条件相同，仅运动反馈连接改变。',
           f'Layered feedback/control evaluation over {duration:g} s: gradient, CheY-P, arrival, contact, conversion and occupancy. '
           'Lines and shading show run means and sample SD. Seed-matched source strength, geometry, boundaries and initial conditions are identical; only motor feedback is toggled.',
           seeds=seeds, cells=8)
    layered_path = spatial / 'M16-layered-timeseries.csv'
    if layered_path.exists():
        layered = rows(layered_path)
        statistics = paired_endpoint_summary(layered, duration, spatial_manifest.get(
            'bootstrap', {'samples': 2000, 'seed': 9201, 'interval': [.025, .975]}))
        (destination / 'manuscript-statistics.json').write_text(
            json.dumps(statistics, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        contacts = []
        for row in layered:
            time = float(row['time_s'])
            contacts.append({key: row[key] for key in ('mechanism', 'feedback', 'seed', 'time_s', 'enrichment_vs_uniform_fluid', 'contact_entries')} |
                            {'contact_entries_per_founder': float(row['contact_entries']) / 8.,
                             'contact_entry_rate_per_founder_s': float(row['contact_entries']) / (8 * time) if time > 0 else None})
        with (destination / 'M16-contact-enrichment.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(contacts[0]))
            writer.writeheader()
            writer.writerows(contacts)
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib import pyplot as plt
        mechanism = sorted({r['mechanism'] for r in layered})[0]
        with plt.rc_context({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False}):
            fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), layout='constrained')
            for feedback in ('True', 'False'):
                group = [r for r in layered if r['mechanism'] == mechanism and r['feedback'] == feedback]
                times = sorted({float(r['time_s']) for r in group})
                for axis, metric, label in zip(axes, ('enrichment_vs_uniform_fluid', 'contact_entries'),
                                               ('Enrichment vs uniform fluid', 'Observed contact entries per run')):
                    values = np.array([[float(r[metric]) for r in group if float(r['time_s']) == t] for t in times])
                    mean, sd = values.mean(axis=1), values.std(axis=1, ddof=1)
                    color = '#398454' if feedback == 'True' else '#537781'
                    axis.plot(times, mean, color=color, label='Feedback' if feedback == 'True' else 'Fixed bias')
                    axis.fill_between(times, mean - sd, mean + sd, color=color, alpha=.15)
                    axis.set(xlabel='Time (s)', ylabel=label)
            axes[0].legend(fontsize=8)
            fig.suptitle(f'M16 supplement · PTS {mechanism.upper()} · Separate occupancy and contact events')
            for suffix in ('png', 'svg'):
                fig.savefig(destination / f'M16-contact-enrichment.{suffix}', dpi=180)
            plt.close(fig)
        figure('M16-contact', destination, 'M16-contact-enrichment',
               f'PTS {mechanism.upper()} 的区域富集倍数与步边界观测到的累计接触进入次数分开显示。线与阴影为运行均值和样本标准差。'
               '趋化关闭对照共用源强、几何、边界和初始条件。频率按进入次数除以细胞数及已模拟时长，另存 CSV。',
               f'PTS {mechanism.upper()} enrichment and observed contact-entry counts are shown separately as means and sample SD. '
               'The fixed-bias control shares source strength, geometry, boundaries and initial conditions. Entry rates per founder and elapsed time are supplied in CSV.',
               seeds=seeds, cells=8)
    summary = rows(signals / 'B-response-summary.csv')
    uncertainty = rows(signals / 'C-uncertainty.csv')
    peak = max(uncertainty, key=lambda r: float(r['transient_gain_per_ln_C_q50']))
    resolution = rows(spatial / 'resolution-check.csv') if (spatial / 'resolution-check.csv').exists() else []
    statuses = {label: sum(r['status'] == label for r in resolution) for label in ('within_threshold', 'exceeds_threshold', 'unresolved_at_this_seed_count')}
    precision = rows(spatial / 'seed-precision.csv') if (spatial / 'seed-precision.csv').exists() else []
    prefix_changes = []
    for key in {(r['mechanism'], r['feedback'], r['metric']) for r in precision}:
        group = sorted([r for r in precision if (r['mechanism'], r['feedback'], r['metric']) == key], key=lambda r: int(r['n_seeds']))
        if len(group) > 1:
            a, b = group[0], group[-1]
            difference = float(b['mean']) - float(a['mean'])
            prefix_changes.append({'mechanism': key[0], 'feedback': key[1], 'metric': key[2],
                                   'seeds_reference': a['n_seeds'], 'seeds_changed': b['n_seeds'], 'observed_mean_change': difference,
                                   'point_change_within_threshold': abs(difference) <= float(b['absolute_allowance']),
                                   'full_sample_precision_within_threshold': b['precision_within_threshold']})
    if prefix_changes:
        with (destination / 'seed-prefix-changes.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(prefix_changes[0]))
            writer.writeheader()
            writer.writerows(prefix_changes)
    states = rows(signals / 'A-dose-response.csv') + rows(signals / 'B-step-response.csv')
    ranges = [{'state': key, 'observed_min': min(float(r[key]) for r in states), 'observed_max': max(float(r[key]) for r in states)}
              for key in ('ei_dephosphorylated_fraction', 'methylation', 'chea_activity', 'chey_p_uM', 'motor_bias')]
    lines = ['# Wiki 模型数据与图位', '',
             f'A/B/C 与空间组采用相同 CAD 源码。空间组共完成 {len(audits)} 份冻结配置运行，均通过逐步收支与信号调用检查。每个配置的 frozen project、时序、轨迹和检查记录保留在 cases 中。', '',
             '本轮 PTS A/B 的有效转运体数量均为 200，处于相同工作点。重合曲线保留其模型标签，统计重复数仍按各自的运动 seed 计算。', '',
             '## A → B → C 的观察', '',
             f'A 的最大归一化稳态推进残差为 {signal_manifest["A"]["equilibrium_residual_max"]:.3g}。开启下游适应后，motor bias 的稳态导数接近零；PTS 状态仍随浓度改变。', '',
             '| 适应 | 阶跃 | 峰值 bias 变化 | 半峰响应时间（s） | 5% 适应时间（s） | 剩余响应比例 |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in summary:
        lines.append(f'| {"开启" if row["adaptation"] == "True" else "关闭"} | {row["phase"]} | {float(row["signed_peak_bias_change"]):.5g} | '
                     f'{float(row["half_peak_response_time_s"]):.5g} | '
                     f'{format(float(row["adaptation_time_5pct_s"]), ".5g") if row["adaptation_time_5pct_s"] else "本时程未回落"} | {float(row["remaining_response_fraction"]):.5g} |')
    lines.extend(['', f'C 的中位瞬态增益在已扫描背景 {float(peak["background_uM"]):.4g} µM 处最高。区间对应 {samples} 组假设参数的响应分布；原始参数和全部背景响应均保留。', '',
                  '## 空间数值与统计检查', '',
                  f'配对设置比较：阈值内 {statuses["within_threshold"]} 项，超出阈值 {statuses["exceeds_threshold"]} 项，当前种子数下未分辨 {statuses["unresolved_at_this_seed_count"]} 项。各项保留指标、配对差值、区间与事先声明的阈值。', '',
                  '种子精度表记录各前缀的区间；seed-prefix-changes.csv 另外记录种子数增加后的均值变化。固定域增加细胞数的结果单列为密度/群体数量扫描。', '',
                  f'全体配置最大化学场收支误差为 {max(abs(float(r["max_field_balance_error"])) for r in audits):.5g} molecule 当量；'
                  f'最大摄取—消耗差值为 {max(abs(float(r["max_uptake_consumption_error"])) for r in audits):.5g}。'
                  '误差与每一步的允许舍入范围一并记录。每个提交步恰好执行一次 PTS 信号更新。', '',
                  '相对梯度统计仅使用高于浓度阈值的探测体素。没有达到浓度阈值的体素时，表中该梯度代理值记为 0，合格体素比例同为 0。', '',
                  '能用于中央差分的探测体素随网格变化，因此网格下的梯度差异包含浓度求解和观测离散化两部分。', '',
                  '## M07：A/B 固定参数扫描中的状态范围', '', '| 状态 | 最小值 | 最大值 |', '| --- | ---: | ---: |'])
    lines.extend(f'| {r["state"]} | {r["observed_min"]:.6g} | {r["observed_max"]:.6g} |' for r in ranges)
    lines.extend(['', '## M10：平台与模块', '',
                  '```mermaid', 'flowchart TB', 'P[冻结项目及模块版本] --> R[Registry / 编译与执行计划]',
                  'R --> M[注册的机制模块]', 'M --> E[ModuleProposal / Effects]',
                  'E --> F[共享物质结算与扩散]', 'F --> G[几何与运动约束]', 'G --> I[身份及生命周期]',
                  'I --> O[观测 / 校验]', 'O --> C[完整步提交]', 'C --> D[记录 / Design 比较及评价]', '```', '',
                  '## M11：核心机制数据流', '', '下图表示核心信息关系；previous_step 连接保留一个已提交步的延迟。'
                  'EI、CheA、甲基化与 CheY-P 是当前 signal.pts_methylation 模块内部的计算关系。完整节点图与边界节点见每个冻结项目。', '',
                  '```mermaid', 'flowchart LR', 'F[糖浓度场] --> S[位置采样]', 'S --> J[摄取请求]', 'N[转运体数量] --> J',
                  'J --> U[共享库存结算]', 'F --> U', 'U --> E[PTS / EI]', 'E --> A[CheA]',
                  'M[甲基化反馈] --> A', 'A --> M', 'A --> Y[CheY-P]', 'Y --> B[Motor bias]',
                  'B -->|previous_step| V[运动]', 'V -->|previous_step| S', 'U --> R[细胞内储备]',
                  'V --> C[材料接触]', 'C --> H[材料转化 / 释放]', 'H --> F', '```', '',
                  '## 图位与中英文图注', ''])
    for figure_row in figures:
        lines.extend([f'### {figure_row["id"]}', '', f'![{figure_row["id"]}]({figure_row["png"]})', '',
                      figure_row['caption_zh'], '', figure_row['caption_en'], '', f'[SVG]({figure_row["svg"]})', ''])
    lines.extend(['## 低对流与实测接入', '',
                  'Péclet 场景表使用流体流速 U，而非游动速度。D=10 µm²/s、L=16 µm 时，Pe≤0.1 要求 U≤0.0625 µm/s；L=64 µm 时要求 U≤0.015625 µm/s。实际装置需结合残余流速测量判断。', '',
                  '文献—模型对应表分别标明机制依据、简化及未验证内容。当前参数区间按假设记录。天然 MCP/aspartate 测量与早期受体改造分开叙述；现有软件芯片预设使用 MeAsp。', '',
                  'M17–M20 和检测精度分层需要实验原图、标注或微流控实测数据。当前报告保留可计算模型的结果，实验图位由对应数据补充。', ''])
    (destination / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    (destination / 'figures.json').write_text(json.dumps(figures, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    identities = {}
    for folder in (signals, spatial):
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                identities[href(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / 'input-sha256.json').write_text(json.dumps(identities, indent=2) + '\n', encoding='utf-8')
    print(f'{len(figures)} figure entries; {len(audits)} spatial cases; report: {destination / "README.md"}')
    return figures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--signals', type=Path, required=True)
    parser.add_argument('--spatial', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build_report(args.signals, args.spatial, args.out)
