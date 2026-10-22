"""Replay retained normal data, then run a NEW 120s normal validation. No 20min collection."""
import argparse
import asyncio
import json
import math
import random
import time
from pathlib import Path

import httpx
from lab_session import load_session, auth_headers, preflight


def historical_window(source):
    training = source['training']
    timestamps = [row['timestamp'] for row in source['validation']]
    if len(timestamps) < 5 or not all(isinstance(t, (int, float)) and math.isfinite(t) for t in timestamps):
        raise ValueError('Missing historical validation timestamps')
    return {'start': training['start'], 'end': training['end'], 'samples': training['samples'],
            'validation_start': min(timestamps), 'validation_end': max(timestamps)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='/reports/dos-calibration.json')
    args = parser.parse_args()
    path = Path(args.report)
    original = json.loads(path.read_text(encoding='utf-8-sig'))
    source = original.get('source_reference') or {
        'training': original['training'], 'validation': original['validation'],
        'started_at': original['started_at'], 'false_alert_fraction': original.get('false_alert_fraction')}
    window = historical_window(source)
    backup = path.with_name('dos-calibration-before-replay-' + str(time.time_ns()) + '.json')
    backup.write_text(json.dumps(original, indent=2), encoding='utf-8')
    report = {'state': 'replaying', 'started_at': time.time(), 'requests': 0,
              'source_reference': source, 'backup': backup.name,
              'fresh_validation_seconds': 120}
    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    save()
    try:
        session = load_session(minimum=600)
        headers = auth_headers(session)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            await preflight(client, session)
            result = await client.post('http://ml-api:8002/replay-baseline', json=window, timeout=60)
            if result.status_code != 200:
                report['replay_refusal'] = result.json()
                raise RuntimeError('Historical replay refused (HTTP ' + str(result.status_code) + '); inspect ML logs')
            replay = result.json()
            report.update(state='validating', training=replay['training'],
                          historical_validation=replay['historical_validation'], validation=[])
            validation_start = time.time()
            report['fresh_validation_started_at'] = validation_start
            save()
            print('Reference historique chargee. Validation normale NOUVELLE : 120 secondes, sans attaque.', flush=True)
            deadline = time.monotonic() + 120
            last = 0
            next_progress = time.monotonic()
            while time.monotonic() < deadline:
                response = await client.get('http://waf:8080/payments', headers=headers)
                response.raise_for_status()
                report['requests'] += 1
                sample = (await client.get('http://ml-api:8002/status')).json()
                timestamp = sample.get('timestamp', 0)
                now = time.time()
                if (isinstance(timestamp, (int, float)) and math.isfinite(timestamp)
                        and validation_start <= timestamp <= now and timestamp > last):
                    last = timestamp
                    # Keep invalid states too: incomplete telemetry must not be counted normal.
                    report['validation'].append(sample)
                if time.monotonic() >= next_progress:
                    print('Validation : ' + str(max(0, round(deadline - time.monotonic()))) + 's restantes', flush=True)
                    next_progress = time.monotonic() + 15
                save()
                await asyncio.sleep(random.uniform(0.7, 1.3))
            samples = report['validation']
            valid = len(samples) >= 5 and all(
                sample.get('state') in ('normal', 'anomaly')
                and sample.get('model_id') == report['training']['model_id'] for sample in samples)
            valid = valid and max((sample.get('timestamp', 0) for sample in samples), default=0) >= time.time() - 45
            fraction = sum(sample.get('state') == 'anomaly' for sample in samples) / len(samples) if samples else None
            report.update(state='ready' if valid and fraction <= 0.2 else 'review_required',
                          false_alert_fraction=fraction, finished_at=time.time())
            save()
            print(json.dumps({'state': report['state'], 'new_validation_samples': len(samples),
                              'false_alert_fraction': fraction, 'model_id': report['training']['model_id']}), flush=True)
            if report['state'] != 'ready':
                raise RuntimeError('Fresh validation did not pass; do not launch the attack')
    except BaseException as exc:
        if report['state'] != 'review_required':
            report.update(state='failed', error_type=type(exc).__name__, finished_at=time.time())
        save()
        raise


if __name__ == '__main__':
    asyncio.run(main())
