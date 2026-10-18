# Reproduce a CAD study

The scripts in this repository use the same registered mechanisms as the application. Each study preserves its input project, time step, grid, seed, metric series and source hashes. Use these records to connect a numerical result with the model and conditions that produced it.

For the complete Wiki A/B/C signal studies, matched spatial controls, M04, OAT/Morris screens, M16 and convergence audits, follow the [Wiki study guide](WIKI_STUDIES.md). The same guide covers the maintained-gradient encounter study, the expression (G) scan and the dark-start release scan. Its [literature-to-model map](WIKI_MODEL_EVIDENCE.md) separates supported mechanisms, simplifications and unvalidated project parameters.

## A first Design study

```bash
python examples/design/compare_speeds.py --out runs/first-design
```

This runs six real Task 0.6 computations: two speeds and one fixed-bias control, each with seeds 0 and 1. It writes a native `study.friskoli`, an HTML report, a CSV report and `evaluation.json`. Open the native package in the application to inspect the saved design and runs.

The objective is signed displacement along the observation axis. The two default seeds include different motion directions. The faster candidate can increase a positive displacement and also increase the magnitude of a negative displacement, so this study can end without a recommendation. Read the paired effects and declared objective together. The program retains the observed outcomes.

## PTS contact and conversion

```bash
python -m pip install ".[research]"
python research/pts_assay.py --out runs/pts-assay-01
```

The default study contains twelve configurations:

- PTS A and B, feedback and matched no-feedback control, at two distinct seeds: eight runs.
- For the first mechanism and seed with feedback: a halved time step, halved grid spacing, and half/double catalytic turnover: four runs.

All cases share the requested physical duration. The short assay places eight cells near four material faces in a 64 × 64 × 32 µm domain. Its default grid is 4 µm, time step 0.02 s and duration 0.4 s. It exercises contact, inventory conversion and numerical comparisons at modest cost. The fine-grid center presets and longer study commands remain available for configuration-specific research.

Choose a new output directory for each study. The script writes:

| Output | Contents |
| --- | --- |
| Numbered JSON records | Frozen project, configuration hash, source hashes, metric series, final metrics and ledger |
| `runs.csv` | One row per configuration with units expressed in column names |
| `paired-effects.csv` | Feedback minus matched no-feedback result for each independent seed |
| `manifest.json` | Environment, source identities, input parameters and study definition |
| `contact-and-conversion.svg` / `.png` | Run means and sample standard deviation over time |
| `sensitivity.svg` / `.png` | Same-seed endpoints at each tested time step, grid and turnover |

Contact time and converted equivalents are separate outputs. Contact uses capsule-to-material surface distance and the declared `contact_range_um`. The contact integral sums occupancy over the initial cohort in cell·s; converted amounts use molecule equivalents. Compare matched seed differences before interpreting ensemble means. A parameter response and numerical sensitivity describe different aspects of the model, so retain the configuration labels in a Wiki figure.

For a longer study with more independent seeds:

```bash
python research/pts_assay.py --duration 2 --dt 0.02 --spacing 4 --seeds 1 2 3 4 --out runs/pts-assay-02
```

The initial dissolved product is sampled from the same analytic concentration field at each grid. Numerical grids can therefore start from different sampled inventories; each run's project and ledger preserve its own quantities. Inspect grid dependence before using interfacial concentration or conversion values quantitatively.

## Supply measured parameters

Use `--parameters measurements.csv` to apply numeric values to existing node parameters. The CSV header is:

```csv
node_id,parameter,value,unit,source,condition
```

For example, a swimming-speed measurement targets node `motility`, parameter `speed_um_s`, with unit `um/s`. Supply the measured value, experiment or dataset identifier, and measurement conditions. The script checks finite values, exact units, unique targets and nonempty provenance, then uses ordinary project validation. The complete supplied rows remain in the study environment record; sensitivity factors are recorded as changes relative to these inputs. This performs parameter substitution, with no automatic parameter fitting.

## Larger center and chip studies

```bash
python research/first-release/center_study.py --release strong --scale small --mechanism a --duration 2 --seeds 4 --dt 0.05 --spacing 1 --out runs/center-study
python research/chip-benchmark/chip_benchmark.py --duration 300 --seeds 8 --workers 1 --out runs/chip-study
```

Use resource-intensive studies serially. The MCP drift measurement uses committed positions in 10–120 s and remains null before sufficient observation time. The 300 s endpoint is a separate measurement. Keep the center and chip configurations separate when reporting their outcomes.

## Verification and reuse

`tests/test_pts_assay.py` checks the reusable assay's paired inputs, physical endpoints, output records, unit checks and Design contact objective. `tests/test_examples.py` checks real Design tasks and package reopening, and builds/runs the module tutorial through the public registry. CI runs these with the numerical and frontend suites. The numerical and functional tests can be repeated without editing the scripts.

For a Wiki figure, retain the CSV, run records and source revision alongside the exported SVG. State the domain, grid, time step, physical duration, parameter variation, number of independent seeds and definition of the displayed uncertainty. Report the model prediction under those conditions and connect it with the corresponding measurement when data are available.
