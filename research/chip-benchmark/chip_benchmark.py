"""Chip benchmark along the horizontal short X axis, with finite MeAsp boundary exchange.

Run from the repository root after `pip install -e .`:

  python research/chip-benchmark/chip_benchmark.py --table       print the parameter table (Markdown)
  python research/chip-benchmark/chip_benchmark.py --quick       3 seeds x 120 s
  python research/chip-benchmark/chip_benchmark.py               8 seeds x 300 s, gradient and zero-gradient control
  python research/chip-benchmark/chip_benchmark.py --set adaptation_rate_s=0.1 --out results-k0.1
  python research/chip-benchmark/chip_benchmark.py --write-preset src/friskoli_cad/examples/chip_mcp_gradient.project.json

Every value comes from src/friskoli_cad/science/data/chip_parameters.json; --set KEY=VALUE overrides one for a run.
"""
import os
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")
import argparse
import csv
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from friskoli_cad.project import simulation_from_project

HERE = Path(__file__).resolve().parent
from friskoli_cad.engine.presets import TABLE, build_chip_project as build_project
SAMPLE_EVERY_S = 10.0


def run_case(job):
    condition, seed, overrides, duration, dt = job
    length = overrides.get("length_um", TABLE["length_um"]["value"])
    sim = simulation_from_project(build_project(condition, seed, overrides), seed=seed)
    series, every, start = [], max(1, int(round(SAMPLE_EVERY_S / dt))), time.perf_counter()
    for i in range(1, int(round(duration / dt)) + 1):
        sim.step(dt)
        if i % every == 0:
            metrics = sim.current.metrics['by_group']['cells']
            series.append((round(i * dt, 6), metrics['region_fraction'], metrics['mean_position_um'] / length if metrics['mean_position_um'] is not None else None))
    x = [c["position_um"][0] for c in sim.current.cell_frame["cells"]]
    return {"condition": condition, "seed": seed, "series": series, "final_x_um": x, "wall_s": time.perf_counter() - start, "metrics": sim.current.metrics["by_group"]["cells"]}


def drift(series, length, t0=10.0, t1=120.0):
    pts = [(t, mx * length) for t, _, mx in series if t0 <= t <= t1]
    return float(np.polyfit([t for t, _ in pts], [x for _, x in pts], 1)[0]) if len(pts) > 1 else float("nan")


def mean_se(values):
    values = np.asarray(values, float)
    return float(values.mean()), float(values.std(ddof=1) / np.sqrt(len(values))) if len(values) > 1 else float("nan")


def markdown_table(overrides):
    rows = ["| parameter | value | unit | category | source |", "| --- | --- | --- | --- | --- |"]
    for key, row in TABLE.items():
        rows.append(f"| {key} | {overrides.get(key, row['value'])} | {row['unit'] or '-'} | {row['category']} | {row['source']} |")
    return "\n".join(rows)


def report(results, out, length, duration):
    out.mkdir(parents=True, exist_ok=True)
    (out / "configuration.json").write_text(json.dumps({"observation_axis": "x", "axis_extent_um": length,
        "duration_s": duration}, indent=2) + "\n", encoding="utf-8")
    with open(out / "timeseries.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["condition", "seed", "t_s", "frac_attractant_side", "mean_x_over_length"])
        for r in results:
            for t, frac, mx in r["series"]:
                w.writerow([r["condition"], r["seed"], t, frac, mx])
    with open(out / "final_positions.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["condition", "seed", "x_um"])
        for r in results:
            for x in r["final_x_um"]:
                w.writerow([r["condition"], r["seed"], x])
    summary = []
    for condition in ("zero", "gradient"):
        runs = [r for r in results if r["condition"] == condition]
        if not runs:
            continue
        frac, mx, vd = (mean_se(x) for x in ([r["series"][-1][1] for r in runs], [r["series"][-1][2] for r in runs], [r["metrics"]["drift_um_s"] if r["metrics"]["drift_um_s"] is not None else float("nan") for r in runs]))
        summary.append([condition, len(runs), *frac, *mx, *vd])
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["condition", "n_seeds", "frac_attractant_side", "se", "mean_x_over_length", "se", "drift_um_s", "se"])
        w.writerows(summary)
    print(f"\ncondition  n  frac_attractant_side   mean_x/L        drift 10-120 s [um/s]   (mean +- SE, t={duration:g} s)")
    for c, n, f1, f2, m1, m2, d1, d2 in summary:
        print(f"{c:9s} {n:2d}  {f1:.3f} +- {f2:.3f}       {m1:.3f} +- {m2:.3f}   {d1:+.3f} +- {d2:.3f}")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    colors = {"zero": "#7a7a7a", "gradient": "#0b6e99"}
    labels = {"zero": "uniform 100 uM (control)", "gradient": "0 to 200 uM gradient"}
    bins = np.linspace(0, length, 17)
    for condition in ("zero", "gradient"):
        runs = [r for r in results if r["condition"] == condition]
        if not runs:
            continue
        t = np.array([s[0] for s in runs[0]["series"]]); frac = np.array([[s[1] for s in r["series"]] for r in runs])
        m, se = frac.mean(axis=0), (frac.std(axis=0, ddof=1) / np.sqrt(len(runs)) if len(runs) > 1 else 0 * frac[0])
        a.plot(t, m, color=colors[condition], label=labels[condition]); a.fill_between(t, m - se, m + se, color=colors[condition], alpha=0.25)
        hist = np.mean([np.histogram(r["final_x_um"], bins=bins)[0] / len(r["final_x_um"]) for r in runs], axis=0)
        b.step(0.5 * (bins[:-1] + bins[1:]) / length, hist, where="mid", color=colors[condition], label=labels[condition])
    a.axhline(0.5, color="k", lw=0.5, ls=":"); a.set(xlabel="time (s)", ylabel="fraction of cells on the high-concentration side", ylim=(0.4, 1.0), title="(a) accumulation over time"); a.legend(frameon=False)
    b.set(xlabel="position along short X axis (x/L; 0 = low, 1 = high)", ylabel="fraction of cells per bin", title=f"(b) distribution at t = {duration:g} s")
    fig.savefig(out / "figure_main.svg"); fig.savefig(out / "figure_main.png", dpi=150); plt.close(fig)
    print(f"\nwrote {out}/summary.csv, timeseries.csv, final_positions.csv, figure_main.svg/png")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override one parameter for this run")
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--dt", type=float)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--out", type=Path, default=HERE / "results")
    parser.add_argument("--quick", action="store_true", help="3 seeds x 120 s")
    parser.add_argument("--table", action="store_true", help="print the parameter table and exit")
    parser.add_argument("--write-preset", type=Path, metavar="FILE", help="write the gradient project (seed 1) for the web UI and exit")
    args = parser.parse_args()
    overrides = {}
    for item in args.set:
        key, _, text = item.partition("=")
        if key not in TABLE:
            sys.exit(f"unknown parameter {key!r}; see --table")
        overrides[key] = type(TABLE[key]["value"])(text)
    if args.table:
        print(markdown_table(overrides)); return
    if args.write_preset:
        args.write_preset.write_text(json.dumps(build_project("gradient", 1, overrides), indent=1), encoding="utf-8")
        print(f"wrote {args.write_preset}"); return
    seeds = 3 if args.quick else args.seeds
    duration = args.duration or (120.0 if args.quick else overrides.get("duration_s", TABLE["duration_s"]["value"]))
    dt = args.dt or overrides.get("dt_s", TABLE["dt_s"]["value"])
    jobs = [(c, s, overrides, duration, dt) for s in range(1, seeds + 1) for c in ("zero", "gradient")]
    workers = args.workers or min(os.cpu_count() or 1, len(jobs))
    print(f"{len(jobs)} runs ({seeds} seeds x 2 conditions), {duration:g} s each, dt={dt:g} s, {workers} workers", flush=True)
    results, start = [], time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(run_case, jobs):
            results.append(r)
            print(f"  done {r['condition']:8s} seed {r['seed']}  ({r['wall_s']:.0f} s)", flush=True)
    print(f"total wall time {time.perf_counter() - start:.0f} s")
    report(results, args.out, overrides.get("length_um", TABLE["length_um"]["value"]), duration)


if __name__ == "__main__":
    main()
