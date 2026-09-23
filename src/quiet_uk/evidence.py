"""Machine-readable capabilities of the historical explorer, not promises of data."""


def evidence_capabilities():
    return {
        'schema_version': 1,
        'source_views': ['road_rail', 'aircraft'],
        'separate_road_and_rail': {'status': 'unavailable', 'reason': 'Published historical bands combine road and rail.'},
        'overall_exposure': {'status': 'unavailable', 'reason': 'Source reference periods and historical metric linkage are unresolved. No validated all-source estimate.'},
        'temporal_metrics': {name: {'status': 'unavailable', 'reason': 'The historical release contains spatial summaries, not temporal observations or event records.'}
                             for name in ('day_night_separation', 'background_level', 'event_maximum', 'event_count', 'quiet_intervals')},
    }


def location_observations(result):
    """Expose semantic distinctions without changing the catalogue's values."""
    definitions = {
        'road_rail_upper_db': ('upper_bound', ['road', 'rail'], 'dB'),
        'airport_reported_lower_db': ('reported_lower_bound', ['airport'], 'dB'),
        'airport_reported_fraction': ('reported_spatial_fraction', ['airport'], 'fraction'),
        'combined_reported_lower_db': ('historical_reported_lower_bound', ['road', 'rail', 'airport'], 'dB'),
    }
    return [{
        'field': field, 'value': result['bands'][field]['value'], 'unit': unit,
        'statistic': statistic, 'sources': sources,
        'qualification': result['bands'][field]['qualification'],
        'value_status': result['bands'][field]['value_status'],
        'metric': 'Lden' if unit == 'dB' else None,
        'metric_evidence': 'configured_only_for_historical_baseline' if unit == 'dB' else 'stored_spatial_fraction',
        'reference_period': None, 'reference_period_status': 'not_established',
        'uncertainty_db': None, 'uncertainty_status': 'not_quantified',
        'temporal_resolution': None, 'temporal_resolution_status': 'no_time_series',
        'spatial_support': result['cell'],
        'provenance': result['dataset'],
    } for field, (statistic, sources, unit) in definitions.items()]
