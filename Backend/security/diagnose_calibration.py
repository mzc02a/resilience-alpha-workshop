"""Read-only reconstruction of a calibration from retained Prometheus telemetry.

Run in the ML image (httpx, numpy, scikit-learn), not the remediator image.
Does not call /train, change thresholds or activate protection.
"""
import argparse
import json
import math
import os
from pathlib import Path

import httpx
import numpy as np
from sklearn.ensemble import IsolationForest

FEATURES = ('requests_per_second', 'errors_per_second', 'mean_latency_seconds')
QUERIES = (
    'sum(rate(http_requests_total{job="payment-api"}[1m]))',
    'sum(rate(http_requests_total{job="payment-api",status=~"5.."}[1m]))',
    'sum(rate(http_request_duration_seconds_sum{job="payment-api"}[1m])) / clamp_min(sum(rate(http_request_duration_seconds_count{job="payment-api"}[1m])), 0.000001)',
    'min(up{job="payment-api"})',
)


def compare(reference, validation):
    values = np.asarray(reference, dtype=float)
    held_out = np.asarray(validation, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) < 60:
        raise ValueError('Need at least 60 complete baseline observations')
    if held_out.ndim != 2 or held_out.shape[1] != 3 or not len(held_out):
        raise ValueError('Missing validation features')
    if not np.isfinite(values).all() or not np.isfinite(held_out).all():
        raise ValueError('Nonfinite telemetry')
    detector = IsolationForest(n_estimators=100, contamination='auto', random_state=42, n_jobs=1)
    detector.fit(values)
    scores = -detector.decision_function(held_out)
    result = {'reconstructed_samples': len(values), 'validation_samples': len(held_out),
              'reconstructed_training_anomaly_fraction': float(np.mean(detector.decision_function(values) < 0)),
              'reconstructed_validation_anomaly_fraction': float(np.mean(scores > 0)),
              'reconstructed_validation_scores': scores.tolist(), 'features': {}}
    for i, name in enumerate(FEATURES):
        low, high = float(values[:, i].min()), float(values[:, i].max())
        alternative = held_out.copy()
        alternative[:, i] = np.median(values[:, i])
        result['features'][name] = {
            'training_min': low, 'training_median': float(np.median(values[:, i])), 'training_max': high,
            'validation_min': float(held_out[:, i].min()), 'validation_max': float(held_out[:, i].max()),
            'validation_outside_training_range': int(np.sum((held_out[:, i] < low) | (held_out[:, i] > high))),
            'sensitivity_anomaly_fraction_if_feature_replaced_by_training_median':
                float(np.mean(detector.decision_function(alternative) < 0)),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='/reports/dos-calibration.json')
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding='utf-8-sig'))
    training = report['training']
    columns = []
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
        for query in QUERIES:
            response = client.get(os.getenv('PROMETHEUS_URL', 'http://prometheus:9090') + '/api/v1/query_range',
                params={'query': query, 'start': training['start'], 'end': training['end'], 'step': 15})
            response.raise_for_status()
            payload = response.json()
            if payload.get('status') != 'success' or payload.get('data', {}).get('resultType') != 'matrix':
                raise ValueError('Invalid Prometheus response')
            series = payload['data']['result']
            if len(series) != 1:
                raise ValueError('Historical telemetry missing or ambiguous; cannot reconstruct baseline')
            columns.append({float(t): float(v) for t, v in series[0]['values'] if math.isfinite(float(v))})
    timestamps = sorted(set.intersection(*(set(column) for column in columns)))
    reference = [[column[t] for column in columns[:3]] for t in timestamps if columns[3][t] == 1]
    validation = [[row['features'][key] for key in FEATURES] for row in report['validation']]
    result = compare(reference, validation)
    result['reported_training_samples'] = training['samples']
    result['same_sample_count'] = len(reference) == training['samples']
    stored = [row.get('score') for row in report['validation']]
    result['matches_reported_scores'] = bool(all(isinstance(value, (int, float)) for value in stored)
        and np.allclose(stored, result['reconstructed_validation_scores'], atol=1e-6, rtol=1e-5))
    result['limitations'] = ('Sensitivity tests are counterfactuals, not proof of root cause. '
                            'A score/sample mismatch prevents claiming an exact reconstruction. '
                            'No model, threshold, calibration report or protection was modified.')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
