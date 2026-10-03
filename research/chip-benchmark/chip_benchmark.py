"""Chip chemotaxis benchmark: MCP-driven E. coli in a stationary linear MeAsp gradient.

Run from the repository root after `pip install -e .`:

  python research/chip-benchmark/chip_benchmark.py --table       print the parameter table (Markdown)
  python research/chip-benchmark/chip_benchmark.py --quick       3 seeds x 120 s
  python research/chip-benchmark/chip_benchmark.py               8 seeds x 300 s, gradient and zero-gradient control
  python research/chip-benchmark/chip_benchmark.py --set adaptation_rate_s=0.1 --out results-k0.1
  python research/chip-benchmark/chip_benchmark.py --write-preset src/friskoli_cad/examples/chip_mcp.project.json

Every value comes from parameters.json; --set KEY=VALUE overrides one for a run.
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

from friskoli_cad.engine.science_extensions import modular_registry
from friskoli_cad.project import simulation_from_project

HERE = Path(__file__).resolve().parent
TABLE = json.loads((HERE / "parameters.json").read_text(encoding="utf-8"))["parameters"]
GRAPH_KIND = {"measured": "measurement", "literature": "literature", "fitted": "calibration", "assumed": "example"}
PROJECT_KIND = {"measured": "measured", "literature": "reference", "fitted": "estimated", "assumed": "example"}
SAMPLE_EVERY_S = 10.0


def provenance(key, overrides, kinds):
    if key in overrides:
        return {"kind": "user" if kinds is GRAPH_KIND else "example", "reference": "command-line override"}
    row = TABLE[key]
    return {"kind": kinds[row["category"]], "reference": row["source"]}


def build_project(condition, seed, overrides=None):
    """Return a modular-spatial-v1 project for 'gradient' (low -> high) or 'zero' (uniform background)."""
    overrides = overrides or {}
    v = {k: overrides.get(k, row["value"]) for k, row in TABLE.items()}
    registry = modular_registry()
    low, high = (v["low_uM"], v["high_uM"]) if condition == "gradient" else (v["background_uM"],) * 2
    length, width, height, step = v["length_um"], v["width_um"], v["height_um"], v["grid_spacing_um"]
    counts = [max(1, round(x / step)) for x in (length, width, height)]
    spacing = [length / counts[0], width / counts[1], height / counts[2]]
    slope = (high - low) / width

    def node(nid, module_id, version, owner_kind, params):
        manifest = registry.get(module_id, version).manifest
        parameters = {}
        for name, (value, key) in params.items():
            entry = {"value": value, "provenance": provenance(key, overrides, GRAPH_KIND)}
            if "unit" in manifest["parameters"][name]:
                entry["unit"] = manifest["parameters"][name]["unit"]
            parameters[name] = entry
        owner = {"kind": owner_kind, "id": "cells" if owner_kind == "population" else nid}
        return {"id": nid, "module_id": module_id, "module_version": manifest["version"], "owner": owner, "parameters": parameters}

    def clamp(nid, y0, y1, target, target_key):
        return node(nid, "field.boundary_exchange", "1.0.0", "environment", {
            "species": ("ligand", "low_uM"), "lower_um": ([0.0, y0, 0.0], "boundary_rate_s"), "upper_um": ([length, y1, height], "boundary_rate_s"),
            "target_um": (target, target_key), "rate_s": (v["boundary_rate_s"], "boundary_rate_s")})

    nodes = [
        node("ligand_field", "field.diffusive_local", "2.0.0", "environment", {
            "species": ("ligand", "low_uM"), "diffusivity_um2_s": (v["diffusivity_um2_s"], "diffusivity_um2_s"),
            "gradient_x_um_per_um": (0.0, "low_uM"), "gradient_y_um_per_um": (slope, "high_uM"), "gradient_z_um_per_um": (0.0, "low_uM")}),
        clamp("low_side", 0.0, spacing[1], low, "low_uM" if condition == "gradient" else "background_uM"),
        clamp("high_side", width - spacing[1], width, high, "high_uM" if condition == "gradient" else "background_uM"),
        node("ligand_sample", "field.sample_local", "1.0.0", "population", {"species": ("ligand", "low_uM")}),
        node("motor_signal", "signal.mcp_adaptation", "2.0.0", "population", {
            "species": ("ligand", "low_uM"), "cluster_size": (v["cluster_size"], "cluster_size"),
            "inactive_binding_um": (v["inactive_binding_um"], "inactive_binding_um"), "active_binding_um": (v["active_binding_um"], "active_binding_um"),
            "methylation_energy": (v["methylation_energy"], "methylation_energy"), "methylation_reference": (v["methylation_reference"], "methylation_reference"),
            "adaptation_rate_s": (v["adaptation_rate_s"], "adaptation_rate_s"), "baseline_activity": (v["baseline_activity"], "baseline_activity"),
            "chea_total_um": (v["chea_total_um"], "chea_total_um"), "chey_total_um": (v["chey_total_um"], "chey_total_um"),
            "chey_phos_per_um_s": (v["chey_phos_per_um_s"], "chey_phos_per_um_s"), "chey_dephos_s": (v["chey_dephos_s"], "chey_dephos_s"),
            "motor_hill": (v["motor_hill"], "motor_hill"), "motor_half_um": (v["motor_half_um"], "motor_half_um"),
            "initial_chey_p_um": (v["chey_total_um"] * v["baseline_activity"] * v["chea_total_um"] * v["chey_phos_per_um_s"]
                                  / (v["chey_phos_per_um_s"] * v["chea_total_um"] * v["baseline_activity"] + v["chey_dephos_s"]), "baseline_activity")}),
        node("motility", "motion.hazard_run_tumble", "1.0.0", "population", {
            "tumble_mode": (v["tumble_mode"], "tumble_mode"), "turn_kernel": (v["turn_kernel"], "turn_kernel"), "speed_um_s": (v["speed_um_s"], "speed_um_s"),
            "minimum_tumble_rate_s": (v["minimum_tumble_rate_s"], "minimum_tumble_rate_s"), "maximum_tumble_rate_s": (v["maximum_tumble_rate_s"], "maximum_tumble_rate_s"),
            "tumble_duration_s": (v["tumble_duration_s"], "tumble_duration_s")}),
    ]
    by_id = {n["id"]: n for n in nodes}

    def edge(a, port_a, b, port_b, timing):
        return {"id": f"{a}_{port_a}_to_{b}_{port_b}", "from": {"node": a, "port": port_a}, "to": {"node": b, "port": port_b}, "timing": timing}
    edges = [edge("ligand_field", "concentration", "ligand_sample", "field", "previous_step"),
             edge("motility", "position", "ligand_sample", "position", "previous_step"),
             edge("ligand_sample", "concentration", "motor_signal", "concentration", "same_step"),
             edge("motor_signal", "motor_bias", "motility", "motor_bias", "previous_step")]
    channels = {}
    for nid, port in (("ligand_sample", "concentration"), ("motor_signal", "motor_bias"), ("motor_signal", "chey_p"), ("motility", "blocked"), ("motility", "turns")):
        manifest = registry.get(by_id[nid]["module_id"], by_id[nid]["module_version"]).manifest
        spec = manifest["outputs"][port]
        channels[f"{nid}.{port}"] = {"node": nid, "port": port, "group_id": "cells", "shape": spec["shape"], "quantity": spec["quantity"], "unit": spec["unit"]}

    rng = np.random.default_rng(seed)
    margin, gap, points = 3.0, 3.0, []
    while len(points) < v["n_cells"]:
        c = np.array([rng.uniform(margin, length - margin), rng.uniform(margin, width - margin), rng.uniform(2.0, height - 2.0)])
        if all(np.linalg.norm(c - q) >= gap for q in points):
            points.append(c)
    quats = rng.normal(size=(len(points), 4))
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    geometry = {"shape": "capsule", "length_um": v["cell_length_um"], "diameter_um": v["cell_diameter_um"],
                "provenance": provenance("cell_length_um", overrides, PROJECT_KIND)}
    name = f"chip-mcp-{condition}"
    return {
        "project_version": "0.6.0", "execution_profile": "modular-spatial-v1", "random_seed": seed, "id": name,
        "domain": {"geometry": "volume", "counts_xyz": counts, "spacing_um_xyz": spacing},
        "species": {"ligand": {"concentration_unit": "uM", "initial_concentration": {
            "value": (low + high) / 2, "unit": "uM", "provenance": provenance("low_uM", overrides, PROJECT_KIND)}}},
        "groups": {"cells": {"ids": [f"cell-{i:04d}" for i in range(len(points))], "positions_um": [p.tolist() for p in points],
                             "orientation_xyzw": quats.tolist(), "initial_geometry": [dict(geometry) for _ in points]}},
        "controls": {},
        "graph": {"protocol_version": "0.2.0", "id": f"{name}-graph", "nodes": nodes, "edges": edges},
        "run": {"protocol_version": "0.1.0", "run_id": f"{name}-run", "graph_id": f"{name}-graph", "groups": ["cells"], "channels": channels},
        "observation": {"id": "high_half", "label": "High-concentration half", "axis": 1,
                        "region_lower_um": [0.0, width / 2, 0.0], "region_upper_um": [length, width, height]},
    }


def run_case(job):
    condition, seed, overrides, duration, dt = job
    width = overrides.get("width_um", TABLE["width_um"]["value"])
    sim = simulation_from_project(build_project(condition, seed, overrides), seed=seed)
    series, every, start = [], int(round(SAMPLE_EVERY_S / dt)), time.perf_counter()
    for i in range(1, int(round(duration / dt)) + 1):
        sim.step(dt)
        if i % every == 0:
            y = np.array([c["position_um"][1] for c in sim.current.cell_frame["cells"]])
            series.append((round(i * dt, 6), float(np.mean(y > width / 2)), float(np.mean(y) / width)))
    y = [c["position_um"][1] for c in sim.current.cell_frame["cells"]]
    return {"condition": condition, "seed": seed, "series": series, "final_y_um": y, "wall_s": time.perf_counter() - start}


def drift(series, width, t0=10.0, t1=120.0):
    pts = [(t, my * width) for t, _, my in series if t0 <= t <= t1]
    return float(np.polyfit([t for t, _ in pts], [y for _, y in pts], 1)[0]) if len(pts) > 1 else float("nan")


def mean_se(values):
    values = np.asarray(values, float)
    return float(values.mean()), float(values.std(ddof=1) / np.sqrt(len(values))) if len(values) > 1 else float("nan")


def markdown_table(overrides):
    rows = ["| parameter | value | unit | category | source |", "| --- | --- | --- | --- | --- |"]
    for key, row in TABLE.items():
        rows.append(f"| {key} | {overrides.get(key, row['value'])} | {row['unit'] or '-'} | {row['category']} | {row['source']} |")
    return "\n".join(rows)


def report(results, out, width, duration):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "timeseries.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["condition", "seed", "t_s", "frac_attractant_side", "mean_y_over_width"])
        for r in results:
            for t, frac, my in r["series"]:
                w.writerow([r["condition"], r["seed"], t, frac, my])
    with open(out / "final_positions.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["condition", "seed", "y_um"])
        for r in results:
            for y in r["final_y_um"]:
                w.writerow([r["condition"], r["seed"], y])
    summary = []
    for condition in ("zero", "gradient"):
        runs = [r for r in results if r["condition"] == condition]
        if not runs:
            continue
        frac, my, vd = (mean_se(x) for x in ([r["series"][-1][1] for r in runs], [r["series"][-1][2] for r in runs], [drift(r["series"], width) for r in runs]))
        summary.append([condition, len(runs), *frac, *my, *vd])
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["condition", "n_seeds", "frac_attractant_side", "se", "mean_y_over_width", "se", "drift_um_s", "se"])
        w.writerows(summary)
    print(f"\ncondition  n  frac_attractant_side   mean_y/W        drift 10-120 s [um/s]   (mean +- SE, t={duration:g} s)")
    for c, n, f1, f2, m1, m2, d1, d2 in summary:
        print(f"{c:9s} {n:2d}  {f1:.3f} +- {f2:.3f}       {m1:.3f} +- {m2:.3f}   {d1:+.3f} +- {d2:.3f}")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    colors = {"zero": "#7a7a7a", "gradient": "#0b6e99"}
    labels = {"zero": "uniform 100 uM (control)", "gradient": "0 to 200 uM gradient"}
    bins = np.linspace(0, width, 17)
    for condition in ("zero", "gradient"):
        runs = [r for r in results if r["condition"] == condition]
        if not runs:
            continue
        t = np.array([s[0] for s in runs[0]["series"]]); frac = np.array([[s[1] for s in r["series"]] for r in runs])
        m, se = frac.mean(axis=0), (frac.std(axis=0, ddof=1) / np.sqrt(len(runs)) if len(runs) > 1 else 0 * frac[0])
        a.plot(t, m, color=colors[condition], label=labels[condition]); a.fill_between(t, m - se, m + se, color=colors[condition], alpha=0.25)
        hist = np.mean([np.histogram(r["final_y_um"], bins=bins)[0] / len(r["final_y_um"]) for r in runs], axis=0)
        b.step(0.5 * (bins[:-1] + bins[1:]) / width, hist, where="mid", color=colors[condition], label=labels[condition])
    a.axhline(0.5, color="k", lw=0.5, ls=":"); a.set(xlabel="time (s)", ylabel="fraction of cells on the high-concentration side", ylim=(0.4, 1.0), title="(a) accumulation over time"); a.legend(frameon=False)
    b.set(xlabel="position across the channel (0 = low side, 1 = high side)", ylabel="fraction of cells per bin", title=f"(b) distribution at t = {duration:g} s")
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
    report(results, args.out, overrides.get("width_um", TABLE["width_um"]["value"]), duration)


if __name__ == "__main__":
    main()
