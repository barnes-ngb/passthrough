"""Drift metrics for driftgauge.

The metric reads zero when nothing changed. That is the Phase 0 gate, and it is
the basis for trusting every later measurement. See specs/spec-01-geometry.md.

What `hausdorff` computes, stated plainly so it is defensible:
    Directed nearest-neighbor distances are taken both ways, A to B and B to A.
    `max` is the symmetric Hausdorff distance (the largest nearest-neighbor gap
    in either direction). `mean` and `median` are taken over the combined set of
    directed distances, giving a typical-deviation reading rather than a worst
    case. Nearest-neighbor matching is used because a reconstructed mesh may be
    sampled differently from the original, so vertex-to-vertex correspondence
    cannot be assumed.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


def nearest_distances(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Distance from each point in `source` to its nearest point in `target`."""
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    tree = cKDTree(target)
    dist, _ = tree.query(source)
    return dist


def hausdorff(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Symmetric drift between two point sets. Returns max, mean, median.

    `max` is the symmetric Hausdorff distance. `mean` and `median` are over the
    combined directed distances. See the module docstring for the definition.
    """
    d_ab = nearest_distances(a, b)
    d_ba = nearest_distances(b, a)
    combined = np.concatenate([d_ab, d_ba])
    return {
        "max": float(combined.max()),
        "mean": float(combined.mean()),
        "median": float(np.median(combined)),
    }
