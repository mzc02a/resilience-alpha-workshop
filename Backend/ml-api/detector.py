"""Learned multivariate novelty detection with explicit measurement noise.

OAS learns correlations on the certified baseline. Unit noise regularization
prevents millisecond fluctuations or constant-zero error counters from producing
singular covariance and spurious alarms. No validation samples enter fit().
"""
import numpy as np
from scipy.stats import chi2
from sklearn.covariance import OAS

MODEL_VERSION = 'oas-noise-v1'
FEATURES = ['requests_per_second', 'errors_per_second', 'mean_latency_seconds']
# Engineering tolerances in physical units; not observations or synthetic data.
# 0.10 request/s, 0.05 error/s, 20 ms. They are fixed before replay, exported in
# metadata, and require revalidation if changed. A chi-square cutoff is a model
# assumption, not a measured 99% detection or accuracy guarantee.
NOISE_SCALES = np.array([0.10, 0.05, 0.020])
REFERENCE_QUANTILE = 0.99


def matrix(values):
    result = np.asarray(values, dtype=float)
    if result.ndim != 2 or result.shape[1] != 3 or not len(result) or not np.isfinite(result).all():
        raise ValueError('Need finite observations with three features')
    return result


class BaselineDetector:
    def fit(self, values):
        values = matrix(values)
        if len(values) < 60:
            raise ValueError('Need at least 60 real baseline observations')
        estimator = OAS().fit(values / NOISE_SCALES)
        self.center_ = estimator.location_.copy()
        self.precision_ = np.linalg.inv(estimator.covariance_ + np.eye(3))
        self.cutoff_ = float(chi2.ppf(REFERENCE_QUANTILE, df=3))
        return self

    def decision_function(self, values):
        delta = matrix(values) / NOISE_SCALES - self.center_
        distance = np.einsum('ij,jk,ik->i', delta, self.precision_, delta)
        # Preserve the API convention: negative decision -> anomaly.
        return 1.0 - np.sqrt(np.maximum(distance, 0) / self.cutoff_)

    def description(self):
        return {'version': MODEL_VERSION, 'algorithm': 'OAS covariance + measurement noise',
                'features': FEATURES, 'noise_scales': NOISE_SCALES.tolist(),
                'reference_quantile': REFERENCE_QUANTILE, 'squared_distance_cutoff': self.cutoff_}
