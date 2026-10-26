"""Freeze synthetic propagation scenarios and analytic limiting-case checks."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.propagation_canary import (BANDS, DIVERGENCE_FACTOR, finite_line_power,
    flat_boundary, midpoint_line_power, point_gain, power_db, sum_powers)


def verified(path):
    path = Path(path).resolve()
    inv = json.loads((path / 'inventory.json').read_bytes())
    for name, digest in inv['files'].items():
        p = (path / name).resolve()
        if not p.is_relative_to(path) or file_hash(p) != digest:
            raise ValueError('Parent integrity failed: ' + name)
    return inv


def run(output, audit):
    output, audit = Path(output).resolve(), Path(audit).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    if not audit.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Audit must be a workspace generation')
    parents = {audit.name: audit, 'ground_barrier_method_review_v1': ROOT / 'artifacts/ground_barrier_method_review_v1'}
    for p in parents.values():
        verified(p)
    hashes = {n: file_hash(p / 'inventory.json') for n, p in parents.items()}
    # Geometry and cases declared in code, without reading reported noise or local sources.
    scenarios = []
    for f in BANDS:
        for g in (0., .3, .7, 1.):
            for label, h in [('absent', None), ('zero_height', 0.), ('1m', 1.), ('2m', 2.),
                             ('3m', 3.), ('6m', 6.), ('60m_cap_stress', 60.), ('unknown_height', None)]:
                screen = None if label == 'absent' else dict(x_m=40., height_m=h)
                result = flat_boundary(f, 100., .05, 4., g, screen=screen)
                scenarios.append(dict(frequency_hz=f, ground_g=g, screen_case=label, screen=screen,
                    source_height_m=.05, receiver_height_m=4., horizontal_distance_m=100., **result,
                    energy_transfer_ratio=None if result['boundary_db'] is None else point_gain(math.hypot(100., 3.95)) * 10 ** (-result['boundary_db'] / 10)))
        result = flat_boundary(f, 100., .05, 4., None)
        scenarios.append(dict(frequency_hz=f, ground_g=None, screen_case='unknown_ground', screen=None,
            source_height_m=.05, receiver_height_m=4., horizontal_distance_m=100., **result, energy_transfer_ratio=None))
    margin = []
    for a in (25., 100., 1000.):
        infinite = DIVERGENCE_FACTOR * math.pi / a
        for half in (500., 1000., 2000., 5000., 10000.):
            exact = finite_line_power(1., half, a)
            numeric = midpoint_line_power(1., half, a, 1.)
            margin.append(dict(perpendicular_distance_m=a, half_length_m=half, power_per_m=1.,
                analytic_power_ratio=exact, midpoint_power_ratio=numeric, numerical_relative_error=abs(numeric / exact - 1),
                infinite_line_fraction=exact / infinite, infinite_line_level_deficit_db=10 * math.log10(infinite / exact),
                interpretation='Unattenuated straight-line geometry stress test, including distances beyond the legacy method scope; not a local acoustic tail bound.'))
    segmentation = []
    exact = finite_line_power(1., 200., 25.)
    for step in (20., 10., 5., 2., 1., .5):
        numeric = midpoint_line_power(1., 200., 25., step)
        segmentation.append(dict(maximum_step_m=step, half_length_m=200., perpendicular_distance_m=25.,
            power_per_m=1., numeric_power_ratio=numeric, analytic_power_ratio=exact,
            relative_error=abs(numeric / exact - 1), level_error_db=10 * math.log10(numeric / exact)))
    point_drop = power_db(point_gain(20.) / point_gain(10.))
    doubling = power_db(sum_powers([point_gain(10.)] * 2) / point_gain(10.))
    held = [s for s in scenarios if s['status'] == 'withheld']
    checks = dict(point_distance_doubling_db=point_drop, incoherent_equal_source_addition_db=doubling,
        infinite_line_distance_doubling_db=10 * math.log10(.5),
        maximum_line_quadrature_relative_error=max(m['numerical_relative_error'] for m in margin),
        finest_segmentation_error_db=segmentation[-1]['level_error_db'],
        all_unknown_height_and_ground_scenarios_withheld=all(s['energy_transfer_ratio'] is None for s in held),
        hard_ground_branch_db=sorted({s['boundary_db'] for s in scenarios if s['ground_g'] == 0 and s['screen_case'] == 'absent'}),
        separate_ground_on_diffraction_branches=sorted({s['separate_ground_db'] for s in scenarios if s.get('branch') == 'diffraction_including_ground'}))
    if not (math.isclose(point_drop, -6.020599913279624, abs_tol=1e-12)
            and math.isclose(doubling, 3.010299956639812, abs_tol=1e-12)
            and checks['maximum_line_quadrature_relative_error'] < 1e-7
            and abs(checks['finest_segmentation_error_db']) < 1e-6
            and checks['hard_ground_branch_db'] == [-3.] and checks['separate_ground_on_diffraction_branches'] == [0.]
            and len(held) == 40):
        raise ValueError('Synthetic limiting-case check failed')
    code_names = ['scripts/55_run_synthetic_propagation.py', 'src/quiet_uk/propagation_canary.py', 'src/quiet_uk/explorer.py']
    protocol = dict(schema_version=1, status='synthetic_canary_only',
        method='JRC72550 VI-12,14-18,21-22,30-32 on exact flat mean planes; 2021/1226 unobstructed-path Rayleigh selector.',
        limitations=['Research subset, not complete current CNOSSOS, an annual indicator or real-area prediction.',
            'Single infinite-width opaque thin screen only; no finite/thick buildings, side paths, wall reflection, transmission or multiple diffraction.',
            'Homogeneous atmosphere only; absorption fixed at zero for analytic isolation, not observed UK conditions; no weather probability averaging.',
            'The full regulatory original/amendment equation chain is not completely qualified. Ground source-side convention follows the checked 2012 VI-31 text.',
            'Terrain probe results and real traffic/source scenarios are not consumed by the synthetic kernel.',
            'Ground coefficients and heights are exact synthetic inputs, not spatial assignments. Hard/soft cases are not universal uncertainty bounds.',
            'Point divergence follows the published rounded +11dB constant (0.00879dB different from exact 10log(4pi)).',
            'The long-line margin test is elementary inverse-square calculus beyond the 2012 2km elementary-path scope, not a CNOSSOS application or real-network cutoff.'],
        geometry=dict(horizontal_distance_m=100., source_height_m=.05, receiver_height_m=4., screen_x_m=40., ground_plane_odn_m=0.),
        source_power='Arbitrary unit synthetic point/line spectral powers. Energy ratios and attenuation only; no reported rasters, actual exposure, A-weighting or annual total.',
        ground_source_g=0., ground_path_cases=[0., .3, .7, 1., None], bands_hz=list(BANDS),
        missing_rule='Unknown ground or encountered obstacle height withholds the result; absence is explicitly synthetic.',
        no_double_count='Only one boundary branch. Pavement corrections stay in emission; no independent extra road reflection gain. Main diffraction cap does not cap image terms.',
        parent_inventory_sha256=hashes, code_sha256={n: file_hash(ROOT / n) for n in code_names},
        runtime=dict(python=sys.version, platform=platform.platform(), numpy=np.__version__, matplotlib=matplotlib.__version__, backend=matplotlib.get_backend()),
        attribution='European Union, JRC72550 (2012), DOI 10.2788/31776; Directive (EU) 2021/1226.',
        real_area_predictions_created=False, eligible_for_absolute_noise_release=False)
    report = dict(**protocol, scenario_count=len(scenarios), conditional_scenarios=len(scenarios) - len(held), withheld_scenarios=len(held),
        analytic_checks=checks, margin_at_5km=[m for m in margin if m['half_length_m'] == 5000.])
    output.mkdir(parents=True)
    for name, document in [('protocol.json', protocol), ('report.json', report), ('boundary_scenarios.json', dict(scenarios=scenarios)),
                            ('line_margin_cases.json', dict(cases=margin)), ('segmentation_cases.json', dict(cases=segmentation))]:
        (output / name).write_bytes(json_bytes(document))
    for n in code_names:
        destination = output / 'code' / n
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, destination)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), constrained_layout=True)
    for g, label in ((0., 'G=0 hard'), (1., 'G=1 porous')):
        for screen, linestyle in (('absent', '--'), ('3m', '-')):
            values = [s for s in scenarios if s['ground_g'] == g and s['screen_case'] == screen]
            axes[0].plot([s['frequency_hz'] for s in values], [s['boundary_db'] for s in values], linestyle=linestyle, marker='.', label=f'{label}; {screen}')
    axes[0].set(xscale='log', xlabel='Frequency (Hz)', ylabel='Boundary attenuation (dB)', title='Ground / one opaque screen')
    axes[0].legend(fontsize=8)
    for a in (25., 100., 1000.):
        values = [m for m in margin if m['perpendicular_distance_m'] == a]
        axes[1].plot([m['half_length_m'] / 1000 for m in values], [m['infinite_line_level_deficit_db'] for m in values], marker='.', label=f'Distance {a:g}m')
    axes[1].set(xlabel='Synthetic line half-length (km)', ylabel='Deficit from infinite line (dB)', title='Source-margin stress test')
    axes[1].legend(fontsize=8)
    axes[2].plot([s['maximum_step_m'] for s in segmentation], [max(abs(s['level_error_db']), 1e-12) for s in segmentation], marker='o')
    axes[2].set(xscale='log', yscale='log', xlabel='Maximum point spacing (m)', ylabel='Absolute error against integral (dB)', title='Line discretisation convergence')
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle('Synthetic propagation canary | geometry and fixed assumptions only; no real-area noise', fontsize=12)
    fig.savefig(output / 'synthetic_propagation_checks.png', dpi=160)
    plt.close(fig)
    for n, digest in hashes.items():
        verified(parents[n])
        if file_hash(parents[n] / 'inventory.json') != digest:
            raise ValueError('Parent changed during experiment')
    for n, digest in protocol['code_sha256'].items():
        if file_hash(ROOT / n) != digest:
            raise ValueError('Code changed during experiment')
    files = {p.relative_to(output).as_posix(): file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output / 'inventory.json').write_bytes(json_bytes(dict(generation_id='synthetic-propagation-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20], files=files)))
    print(json.dumps(dict(scenarios=len(scenarios), withheld=len(held), checks=checks, margin_at_5km=report['margin_at_5km'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/synthetic_propagation_v2'))
    parser.add_argument('--audit', default=str(ROOT / 'artifacts/ground_barrier_audit_v1'))
    args = parser.parse_args()
    run(args.output, args.audit)
