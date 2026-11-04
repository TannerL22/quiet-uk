"""Versioned interpretation of source encodings; never infer censoring from a crop."""
import math

VERSION = 1
STATES = {
    'reported_model_value': {'label': 'Modelled value', 'description': 'A reported model value for this source and indicator; not a measurement of all sound.'},
    'verified_below_cutoff': {'label': 'Below reporting cutoff', 'description': 'A source-specific upper bound supported by encoding and domain evidence; not an exact value.'},
    'unreported_unknown': {'label': 'Unknown', 'description': 'No defensible value or upper bound. A zero code or missing cell is not silence.'},
    'outside_extract': {'label': 'Outside coverage', 'description': 'Outside this downloaded area; no claim about the provider domain or sound level.'},
    'outside_domain': {'label': 'Outside model domain', 'description': 'Confirmed outside the applicable model domain; not evidence of silence.'},
}
CONTRACT = {
    'version': VERSION, 'states': STATES,
    'zero_encoding_status': 'unverified',
    'domain_status': 'no_authoritative_cell_domain_mask',
    'missing_value_policy': 'Zero codes and TIFF nodata remain distinct encodings of unknown exposure. Neither establishes a below-cutoff bound. Outside the extract is not outside the model domain.',
    'bounds_note': 'Bounds describe model evidence, not confidence intervals or measured exposure. Null endpoints are unbounded; null bounds mean unavailable.',
    'display_missing': [
        {'encoding': 'zero_code', 'label': 'Zero code · level unknown', 'colour': '#c0b8a5'},
        {'encoding': 'tiff_nodata', 'label': 'Missing cell · level unknown', 'colour': '#aab5c4'},
    ],
}


# Defra's reply to the 3 October 2026 request defines the published road/rail
# encodings. Aircraft cutoffs are airport-specific and its grids carry no zeros.
PROVIDER_REPLY = {
    'note': 'notes/DEFRA_DATA_REQUEST_2026-10-03.md#provider-reply',
    'retained_text': 'artifacts/provider_request_2026-10-03/reply-owner-pasted.txt',
    'retained_text_sha256': '7f319ee0452d5beab058f8ceb9f41ae6799300e07a511a6d5f8c7c9d0fd236f1',
    'zero': 'Numeric zero marks a calculated level below the threshold, set to 0.',
    'nodata': '-96 is a no data value.',
    'cutoffs': 'Applied without rounding and inclusive: 40 dB Lden is retained, as is 35 dB Lnight.',
    'below_cutoff_values': 'Not held by Defra.',
}
ZERO_RULE_SOURCES = ('road', 'rail')


def zero_rule(source, metric):
    """The provider-defined zero bound for one layer, or None where zero stays unknown."""
    if source not in ZERO_RULE_SOURCES:
        return None
    return {'status': 'verified', 'cutoff_db': 35 if metric == 'Lnight' else 40,
            'evidence_ref': PROVIDER_REPLY['note'], 'evidence_sha256': PROVIDER_REPLY['retained_text_sha256'],
            'zero_means': 'calculated level below an inclusive cutoff, so strictly below it'}


CONTRACT_V2 = {
    'version': 2, 'states': STATES,
    'zero_encoding_status': 'provider_defined_for_road_and_rail',
    'provider_reply': PROVIDER_REPLY,
    'zero_rule_sources': list(ZERO_RULE_SOURCES),
    'domain_status': 'zero_codes_are_calculated_cells; no_provider_mask_for_nodata',
    'missing_value_policy': ('Road and rail zero codes are calculated levels below the inclusive reporting cutoff, '
                             'so they are shown as an upper bound (under 40 dB, under 35 dB at night), never as a value. '
                             'TIFF nodata (-96) has no published value and stays unknown. Aircraft zeros, if any, stay unknown. '
                             'Outside the downloaded area is not a claim about the provider domain or sound level.'),
    'bounds_note': CONTRACT['bounds_note'],
    'display_missing': [
        {'encoding': 'zero_code', 'label': 'Under {cutoff} dB · below the published cutoff', 'colour': '#d6ecea'},
        {'encoding': 'tiff_nodata', 'label': 'No data · not published', 'colour': '#aab5c4'},
    ],
}


def record_evidence(record, product, files):
    minimum = 35 if record['metric'] == 'Lnight' else 40
    aircraft = record['source'] == 'aircraft'
    metadata = {'path': product['metadata_file'], 'sha256': files[product['metadata_file']],
                'url': 'https://environment.data.gov.uk/dataset/' + product['metadata_id']}
    return {
        'contract_version': VERSION,
        'provider_minimum_reporting_level_db': minimum,
        'reporting_cutoff_db': None if aircraft else minimum,
        'reporting_cutoff_status': 'airport_specific_not_established' if aircraft else 'provider_declared',
        'threshold_evidence': metadata,
        'observed_min_reported_db': record['qa']['reported_min_db'],
        'observed_min_scope': {'record_id': record['id'], 'source_sha256': record['sha256'], 'bounds_bng': record['qa']['bounds']},
        'observed_min_note': 'Minimum in this extract only; never a ceiling on missing values.',
        'zero_encoding_status': CONTRACT['zero_encoding_status'],
        'domain_status': CONTRACT['domain_status'],
        'period_evidence': {'status': product['reference_period_status'],
                            'provider_declared_period': product['reference_period'],
                            'assessed_period': None, 'metadata': metadata},
    }


def interpret(raw, *, inside, masked=False, domain=None, verified_zero_rule=None):
    """Keep encoding separate from meaning.

    A censoring rule needs a finite cutoff, an explicit evidence reference, and
    positive domain membership. A provider bounding box is not such evidence; the
    provider's definition of zero as a calculated result is (see zero_rule).
    """
    bounds, value = None, None
    if not inside:
        encoding, status = 'not_sampled', 'outside_extract'
    else:
        if raw is None or not math.isfinite(raw):
            raise ValueError('An in-extract sample must be finite')
        encoding = 'tiff_nodata' if masked else 'zero_code' if raw == 0 else 'numeric'
        if domain is False:
            status = 'outside_domain'
        elif masked:
            status = 'unreported_unknown'
        elif raw == 0:
            status = 'unreported_unknown'
            if verified_zero_rule is not None:
                cutoff = verified_zero_rule.get('cutoff_db')
                if (domain is not True or verified_zero_rule.get('status') != 'verified'
                        or not verified_zero_rule.get('evidence_ref') or isinstance(cutoff, bool)
                        or not isinstance(cutoff, (float, int)) or not math.isfinite(cutoff) or not 0 < cutoff <= 160):
                    raise ValueError('A verified zero bound requires cutoff, encoding evidence and domain membership')
                status = 'verified_below_cutoff'
                bounds = {'lower_db': None, 'upper_db': cutoff, 'lower_inclusive': False, 'upper_inclusive': False}
        elif 0 < raw <= 160:
            status, value = 'reported_model_value', raw
            bounds = {'lower_db': raw, 'upper_db': raw, 'lower_inclusive': True, 'upper_inclusive': True}
        else:
            raise ValueError('Unrecognised unmasked noise encoding')
    explanation = STATES[status]
    label = explanation['label']
    if status == 'verified_below_cutoff':
        label = f'Under {bounds["upper_db"]:g} dB(A)'
    return {'status': status, 'raw_encoding': encoding, 'value_db': value,
            'interpretation_evidence': dict(verified_zero_rule) if status == 'verified_below_cutoff' else None,
            'bounds': bounds, 'display_label': label, 'explanation': explanation['description']}


def difference_bounds(first, other):
    """Other minus first, preserving strict inequalities and unbounded endpoints."""
    a, b = first.get('bounds'), other.get('bounds')
    supported = {'reported_model_value', 'verified_below_cutoff'}
    if first['status'] not in supported or other['status'] not in supported or a is None or b is None:
        return None
    lower = None if b['lower_db'] is None or a['upper_db'] is None else b['lower_db'] - a['upper_db']
    upper = None if b['upper_db'] is None or a['lower_db'] is None else b['upper_db'] - a['lower_db']
    if lower is None and upper is None:
        return None
    return {'lower_db': lower, 'upper_db': upper,
            'lower_inclusive': lower is not None and b['lower_inclusive'] and a['upper_inclusive'],
            'upper_inclusive': upper is not None and b['upper_inclusive'] and a['lower_inclusive']}
