# Third-party notices

Friskoli-CAD is distributed under the [MIT License](LICENSE). Third-party software, fonts and dependencies retain their own copyrights and licenses. This notice identifies bundled assets and directly declared dependencies for version 1.0.1; it does not replace original license texts.

## Bundled browser assets

| Component | Version | Use and source | License and retained notice |
| --- | --- | --- | --- |
| Three.js | r180 | 3D rendering, OrbitControls and TransformControls; [upstream](https://github.com/mrdoob/three.js/tree/r180) | MIT; [original LICENSE](src/friskoli_cad/web/vendor/three/LICENSE) |
| KaTeX JavaScript and CSS | 0.18.9 | Mathematical typesetting; [upstream](https://github.com/KaTeX/KaTeX), [package](https://registry.npmjs.org/katex/-/katex-0.18.9.tgz) | MIT; [original LICENSE](src/friskoli_cad/web/vendor/katex/LICENSE) |
| KaTeX fonts | From the bundled 0.18.9 package | TTF, WOFF and WOFF2 math fonts | SIL Open Font License 1.1, as declared in font metadata; [copyrights, reserved names and full license](src/friskoli_cad/web/vendor/katex/fonts/OFL.txt) |

Three.js retains `Copyright © 2010-2025 three.js authors`. KaTeX software retains `Copyright (c) 2013-2020 Khan Academy and other contributors`. Fonts retain copyrights of Design Science, Inc. (2009-2010) and Khan Academy (2014-2018), with reserved names listed in `fonts/OFL.txt`. The [upstream clarification](https://github.com/KaTeX/KaTeX/issues/339) distinguishes font and software licensing. The [official OFL text](https://openfontlicense.org/open-font-license-official-text/) supplies the terms.

Runtime files are vendored under `src/friskoli_cad/web/vendor/` and included in the wheel. The upstream JavaScript, CSS and font binaries are unchanged. The added human-readable OFL supplements existing font metadata. Static viewers copy the Three.js assets they use with the original license. Runtime rendering makes no external CDN request.

## Python runtime dependencies

These dependencies are installed separately by pip, not vendored in the Friskoli-CAD wheel. Requirements come from `pyproject.toml`. Ranges permit different installed releases; retain notices supplied by the actual installed distributions.

| Dependency | Requirement | Purpose | Upstream license |
| --- | --- | --- | --- |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | `>=4.23,<5` | JSON contract validation | MIT |
| [NumPy](https://github.com/numpy/numpy) | `>=1.26,<3` | Numerical arrays | BSD-3-Clause; binaries may carry additional notices |
| [rfc8785](https://github.com/trailofbits/rfc8785.py) | `==0.1.4` | Canonical JSON and hashes | Apache-2.0; [versioned license](https://github.com/trailofbits/rfc8785.py/blob/v0.1.4/LICENSE) |
| [psutil](https://github.com/giampaolo/psutil) | `==7.2.2` | Worker resource monitoring | BSD-3-Clause |

## Optional and development dependencies

| Dependency | Requirement | Purpose | Upstream license |
| --- | --- | --- | --- |
| [pySBOL3 / sbol3](https://github.com/SynBioDex/pySBOL3) | `==1.2.0.post0` | SBOL validation/export | MIT |
| [CuPy / cupy-cuda11x](https://github.com/cupy/cupy) | `==13.6.0` | CUDA diffusion | MIT; accompanying CUDA components retain their own notices |
| [Matplotlib](https://github.com/matplotlib/matplotlib) | `>=3.8,<4` | Research plots | Matplotlib license agreement, based on the PSF license; bundled assets carry additional notices |
| [pytest](https://github.com/pytest-dev/pytest) | `>=9,<10` | Python tests | MIT |
| [build](https://github.com/pypa/build) | `>=1.2,<2` | Distribution builds | MIT |
| [setuptools](https://github.com/pypa/setuptools) | `>=77` | Build backend | MIT |
| [wheel](https://github.com/pypa/wheel) | Build dependency | Wheel packaging | MIT |
| [fake-indexeddb](https://github.com/dumbmatter/fakeIndexedDB) | `6.2.5` | Frontend persistence tests | Apache-2.0 |

Transitive dependencies, Python/Node.js runtimes and optional hardware libraries carry additional notices in their own distributions. A complete environment redistribution must retain those notices alongside this file and the bundled licenses.

## Scientific sources

References and parameter provenance are identified in [the model reference](docs/MODEL_REFERENCE.md#scientific-assumptions), `src/friskoli_cad/science/data/` and module declarations. A citation does not place a paper or its figures under the project's MIT license or imply author endorsement. The team's own A/B model material belongs to the project license; any third-party implementation or dataset added later retains its original terms.

## Maintaining this notice

For bundled updates, record the exact version and origin, retain original copyright/license texts and check font/binary notices. Verify that the wheel and source distribution include this file and bundled licenses. For separately installed dependencies, inspect resolved distribution metadata and license files; the table is not a complete license inventory for every installed environment.
