"""The reverse-problem boundary experiment. See specs/spec-06-boundary.md.

This is an experiment, not a feature. The instrument carries the UV
parameterization, the per-vertex identity, and the topology across the boundary,
which makes reconstruction a well-conditioned least-squares fit with known
correspondence. This module removes the carried information rung by rung and
measures what happens, to locate where reconstruction stops being well-posed.

A result of "this does not converge" or "this is ill-posed" is a valid finding,
not a defect. The code does not force a rung to look good. It reports what
happens.

Honesty framing (AGENT.md):
    The measurement reuses the category 1 meters (driftgauge, constraints) and the
    category 2 classical reconstructor unchanged. Only the parameterization and the
    correspondence are stripped, rung by rung. The new logic here is the
    parameterization estimation (chord-length, PCA-plane), authored and named as
    assumptions. This module does not implement the learned reconstructor, which
    stays category 3 and unbuilt (AGENT.md hard do-not list).

The charter boundary held (AGENT.md "The boundary that must not move"):
    Estimating a parameterization or guessing a correspondence walks up to the
    parameterization-gap and reverse-problem frontiers. It does not cross them. The
    estimates are deliberately simple and are reported failing. Measuring how far
    reconstruction degrades once carried data is gone is instrument work. Closing
    that gap is the solver work this project does not claim.

The ladder, most to least carried information:
    1. Full carry      carried UV + identity + topology (the current behavior).
    2. Estimated UV    identity + topology; UV estimated from the mesh geometry.
    3. No correspondence  an unordered, shuffled point cloud; UV and correspondence
                          both guessed.
    4. Changed topology   a remesh; the validity gate stops it before any fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import rhino3dm as r3

from passthrough.constraints import (
    ConstraintResult,
    check_constraints,
    curvature_residual,
)
from passthrough.driftgauge import hausdorff
from passthrough.encode import Descriptor, topology_from_mesh
from passthrough.geometry import Mesh, build_wing_surface, tessellate
from passthrough.reconstruct import ClassicalReconstructor
from passthrough.validity import validity_gate


# --- experiment configuration ---------------------------------------------
#
# The grid and the reconstructor match Phase 1 exactly, so rung 1 reproduces the
# Phase 1 honest-fit drift on the same ground truth. The remesh grid is a coarser
# tessellation of the same wing: a genuine connectivity change with a different
# vertex count, the realistic form of the remesh Phase 4 flagged.

N_U, N_V = 40, 16
N_CTRL_U, N_CTRL_V = 7, 4
REMESH_U, REMESH_V = 30, 12
DEFAULT_SEED = 0

# A curvature-class budget for the constraint meter. With the coarse 7x4 basis the
# baseline curvature residual already exceeds a tight budget (the representation gap
# established in Phase 2 and 3), so this is reported, not tuned to pass. The number
# is held fixed across the ladder so the rungs are compared on one budget.
CURVATURE_TOLERANCE = 1.0


# --- result type ----------------------------------------------------------


@dataclass
class RungResult:
    """One rung of the ladder, measured. See spec-06.

    rung:        the rung number, 1 to 4.
    name:        the short rung name.
    carried:     what information this rung carries, for the summary table.
    status:      "reconstructed" (a usable fit), "ill_posed" (a fit was produced but
                 rests on an arbitrary assumption and does not represent the surface),
                 or "blocked" (no fit ran; the validity gate stopped it).
    drift:       the max/mean positional drift summary, or None when no fit ran.
    curvature:   the curvature residual summary, or None when no fit ran.
    constraints: the constraint results, empty when no fit ran.
    reason:      a plain statement of what happened. Required on ill-posed and
                 blocked rungs; carried on every rung.
    detail:      secondary numbers: the assumption parameters, the gate signal, counts.
    """

    rung: int
    name: str
    carried: str
    status: str
    reason: str
    drift: dict[str, float] | None = None
    curvature: dict[str, float] | None = None
    constraints: list[ConstraintResult] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def reconstructed(self) -> bool:
        """True when a fit ran and produced drift, whether or not it is well-posed."""
        return self.drift is not None


# --- parameterization estimation (the stripped-information rungs) ----------


def estimate_grid_uv(vertices: np.ndarray, n_u: int, n_v: int) -> np.ndarray:
    """Chord-length parameterization on the carried grid (rung 2).

    The carried UV is discarded; the grid topology is kept. Vertices laid out
    row-major as geometry.tessellate writes them (index = a * n_v + b) reshape to an
    (n_u, n_v, 3) grid. The u parameter at each grid row is the cumulative edge
    length down its column, normalized to [0, 1]; the v parameter at each grid column
    is the cumulative edge length across its row, normalized; each is averaged over
    the other index so one parameter value serves the whole row or column.

    Assumption stated (spec-06): the grid structure is known (rung 2 keeps the
    topology) and the rulings of the parameterization run along the grid lines. The
    estimate departs from the carried uniform UV because the wing is not uniformly
    spaced in arc length: its section clusters samples near the leading edge. The
    fixed clamped-uniform knots were placed for the uniform parameterization, so the
    estimated parameters no longer line up with the knots and the fit degrades.
    """
    grid = np.asarray(vertices, dtype=float).reshape(n_u, n_v, 3)

    du = np.linalg.norm(np.diff(grid, axis=0), axis=2)  # (n_u-1, n_v)
    u_cum = np.vstack([np.zeros(n_v), np.cumsum(du, axis=0)])  # (n_u, n_v)
    u_cum /= u_cum[-1]  # normalize each column to [0, 1]
    u_param = u_cum.mean(axis=1)  # (n_u,)

    dv = np.linalg.norm(np.diff(grid, axis=1), axis=2)  # (n_u, n_v-1)
    v_cum = np.hstack([np.zeros((n_u, 1)), np.cumsum(dv, axis=1)])  # (n_u, n_v)
    v_cum /= v_cum[:, -1:]  # normalize each row to [0, 1]
    v_param = v_cum.mean(axis=0)  # (n_v,)

    uv = np.empty((n_u * n_v, 2))
    for a in range(n_u):
        for b in range(n_v):
            uv[a * n_v + b] = (u_param[a], v_param[b])
    return uv


def pca_plane_uv(points: np.ndarray) -> tuple[np.ndarray, dict]:
    """Parameterization guessed from an unordered point cloud (rung 3).

    There is no correspondence and no carried UV, so a parameterization is imposed.
    The points are projected onto their best-fit plane (the two largest principal
    directions). The principal-axis signs are fixed by the standard SVD convention
    (the largest-magnitude loading of each axis is made positive), so the result is
    deterministic. The two in-plane directions are assigned to v and u by spatial
    extent (the wing is longer in span than in chord). Each in-plane coordinate is
    normalized to [0, 1] as its parameter.

    Every step is an arbitrary assumption (the plane, the axis assignment, the sign
    convention, the linear normalization), stated plainly because the point of rung 3
    is that the assumption does not hold. The recovery uses no vertex order, so it is
    invariant to the shuffle; that invariance is the honest statement that no
    correspondence was used.

    Returns the (N, 2) uv and a detail dict with the singular values and the assigned
    extents, so the assumption is visible in the report.
    """
    points = np.asarray(points, dtype=float)
    centroid = points.mean(axis=0)
    centered = points - centroid

    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    # Deterministic sign: make each axis's largest-magnitude loading positive.
    max_abs = np.argmax(np.abs(vt), axis=1)
    signs = np.sign(vt[np.arange(vt.shape[0]), max_abs])
    signs[signs == 0] = 1.0
    vt = vt * signs[:, None]

    proj = centered @ vt.T  # coordinates in the principal frame
    axis0, axis1 = proj[:, 0], proj[:, 1]

    # Larger spatial extent is the span (v); the smaller is the chord (u).
    if np.ptp(axis0) >= np.ptp(axis1):
        v_coord, u_coord = axis0, axis1
    else:
        v_coord, u_coord = axis1, axis0

    def norm01(x: np.ndarray) -> np.ndarray:
        lo, hi = float(x.min()), float(x.max())
        span = hi - lo
        return (x - lo) / span if span > 0 else np.zeros_like(x)

    uv = np.column_stack([norm01(u_coord), norm01(v_coord)])
    detail = {
        "singular_values": [float(s) for s in singular[:3]],
        "u_extent": float(np.ptp(u_coord)),
        "v_extent": float(np.ptp(v_coord)),
    }
    return uv, detail


# --- the shared measurement -----------------------------------------------


def _curvature_descriptor() -> Descriptor:
    """The curvature-class budget the constraint meter reads, held across the ladder."""
    return Descriptor(
        constraints=[{"type": "curvature", "tolerance": CURVATURE_TOLERANCE}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "experiment": "phase6_boundary"},
    )


def _measure(
    surface: r3.NurbsSurface,
    source_mesh: Mesh,
    source_surface: r3.NurbsSurface,
) -> tuple[dict[str, float], dict[str, float], list[ConstraintResult]]:
    """Drift, curvature residual, and constraint preservation for one reconstruction.

    The same three meters for every reconstructed rung, so the rungs are comparable.
    Drift is the symmetric Hausdorff between the source tessellation and the
    reconstruction re-tessellated on the same grid: the Phase 1 fit-only comparison.
    The curvature residual and the constraint check compare against the source
    surface. No solver deformation is involved; the experiment isolates the carried
    information, not a deformation.
    """
    remesh = tessellate(surface, N_U, N_V)
    drift = hausdorff(source_mesh.vertices, remesh.vertices)
    curvature = curvature_residual(surface, source_surface)
    constraints = check_constraints(
        _curvature_descriptor(), surface, source=source_surface
    )
    return drift, curvature, constraints


# --- the rungs ------------------------------------------------------------


def rung_1_full_carry(
    source_mesh: Mesh, source_surface: r3.NurbsSurface
) -> RungResult:
    """Carried UV, identity, and topology: the current ClassicalReconstructor.

    Reconstruct from the source points and the carried per-vertex UV, exactly as the
    loop does. Expected: tight, representation-gap drift only. This reproduces the
    Phase 1 honest-fit drift on the same grid and reconstructor.
    """
    rc = ClassicalReconstructor(N_CTRL_U, N_CTRL_V)
    surface = rc.reconstruct(source_mesh.vertices, source_mesh.uv)
    drift, curvature, constraints = _measure(surface, source_mesh, source_surface)
    return RungResult(
        rung=1,
        name="full carry",
        carried="carried UV + identity + topology",
        status="reconstructed",
        reason=(
            "Carried UV gives known correspondence and a fixed parameterization, so "
            "the fit is a well-conditioned least-squares solve. The only drift is the "
            "representation gap of the fixed 7x4 basis (the Phase 1 honest-fit floor)."
        ),
        drift=drift,
        curvature=curvature,
        constraints=constraints,
    )


def rung_2_estimated_uv(
    source_mesh: Mesh, source_surface: r3.NurbsSurface
) -> RungResult:
    """Discard the carried UV; estimate it from the mesh on the carried grid.

    Identity and topology are kept, so the grid structure is known. The UV is the
    chord-length parameterization along the grid lines (estimate_grid_uv). Expected:
    drift increases. This is the parameterization gap made measurable.
    """
    rc = ClassicalReconstructor(N_CTRL_U, N_CTRL_V)
    uv = estimate_grid_uv(source_mesh.vertices, N_U, N_V)
    surface = rc.reconstruct(source_mesh.vertices, uv)
    drift, curvature, constraints = _measure(surface, source_mesh, source_surface)
    return RungResult(
        rung=2,
        name="estimated UV",
        carried="identity + topology; UV estimated (chord-length)",
        status="reconstructed",
        reason=(
            "The carried UV is gone; a chord-length parameterization is estimated on "
            "the carried grid. It differs from the uniform carried UV because the wing "
            "section clusters near the leading edge, so the estimated parameters no "
            "longer line up with the fixed knots and the fit degrades."
        ),
        drift=drift,
        curvature=curvature,
        constraints=constraints,
    )


def rung_3_point_cloud(
    source_mesh: Mesh,
    source_surface: r3.NurbsSurface,
    seed: int = DEFAULT_SEED,
) -> RungResult:
    """An unordered, shuffled point cloud: guess both correspondence and UV.

    The vertices are shuffled under a fixed seed and the topology is dropped. A
    parameterization is imposed by PCA-plane projection (pca_plane_uv). Expected:
    ill-posed. The reconstruction departs from the surface by a distance on the order
    of the part's own size, which is the honest reading that the imposed assumption
    does not hold. The numbers are reported as evidence, not as a result to defend.
    """
    rng = np.random.default_rng(seed)
    perm = rng.permutation(source_mesh.vertices.shape[0])
    points = source_mesh.vertices[perm]

    rc = ClassicalReconstructor(N_CTRL_U, N_CTRL_V)
    uv, pca_detail = pca_plane_uv(points)
    surface = rc.reconstruct(points, uv)
    drift, curvature, constraints = _measure(surface, source_mesh, source_surface)

    # The part's own size, for stating the drift relative to it.
    extent = float(np.linalg.norm(np.ptp(source_mesh.vertices, axis=0)))
    detail = {"seed": int(seed), "part_extent": extent, **pca_detail}
    return RungResult(
        rung=3,
        name="no correspondence",
        carried="unordered point cloud (no UV, no identity, no topology)",
        status="ill_posed",
        reason=(
            "No correspondence to recover the parameterization from. A PCA-plane "
            "parameterization is imposed with an arbitrary axis assignment and sign "
            f"convention. The drift is order the part's own size ({extent:.2f} mm), so "
            "the reconstruction does not represent the surface. The recovery uses no "
            "vertex order, so it is invariant to the shuffle: no correspondence was used."
        ),
        drift=drift,
        curvature=curvature,
        constraints=constraints,
        detail=detail,
    )


def rung_4_remesh(
    source_mesh: Mesh,
    source_surface: r3.NurbsSurface,
    tolerance: float = 0.01,
) -> RungResult:
    """A remesh: different connectivity from what was sent. The gate stops it.

    The wing is re-tessellated on a coarser grid, the realistic form of the remesh
    Phase 4 flagged as identity-not-preserved (Phase 4 simulated it by dropping a
    face; a resolution change is the same signal and also breaks the vertex
    cardinality). The validity gate runs first against the sent topology and stops on
    identity-not-preserved, so reconstruction never runs.

    The deeper reason is shown by forcing it: the carried UV has one entry per sent
    vertex, the remesh has a different vertex count, so the carried correspondence
    does not exist for the remeshed vertices and the reconstructor refuses the call.
    The carried UV is structurally unusable across a topology change, not merely
    inaccurate.
    """
    sent_topology = topology_from_mesh(source_mesh)
    remesh = tessellate(source_surface, REMESH_U, REMESH_V)
    returned_topology = topology_from_mesh(remesh)

    gate = validity_gate(sent_topology, remesh, returned_topology, tolerance)
    identity = gate.results[0]

    # Show the carried UV cannot be applied: the cardinality does not match.
    forced_error = None
    try:
        ClassicalReconstructor(N_CTRL_U, N_CTRL_V).reconstruct(
            remesh.vertices, source_mesh.uv
        )
    except ValueError as exc:
        forced_error = str(exc)

    return RungResult(
        rung=4,
        name="changed topology",
        carried="remesh: connectivity differs from what was sent",
        status="blocked",
        reason=(
            "The validity gate stops on identity-not-preserved before any fit runs. "
            f"Forcing it confirms the deeper reason: the carried UV has "
            f"{source_mesh.vertices.shape[0]} entries, the remesh has "
            f"{remesh.vertices.shape[0]} vertices, so the carried correspondence does "
            "not exist for the remeshed vertices and the reconstructor refuses the call."
        ),
        detail={
            "gate_valid": bool(gate.valid),
            "gate_signals": list(gate.signals),
            "n_sent": int(source_mesh.vertices.shape[0]),
            "n_returned": int(remesh.vertices.shape[0]),
            "identity_detail": dict(identity.detail),
            "forced_carried_uv_error": forced_error,
        },
    )


# --- the ladder -----------------------------------------------------------


def run_ladder(seed: int = DEFAULT_SEED) -> list[RungResult]:
    """Run all four rungs on the shared wing ground truth. Returns one result each.

    Deterministic given the seed: the only randomness is the rung 3 shuffle. Nothing
    crashes on the ill-posed or blocked rungs; they return a structured result.
    """
    source_surface = build_wing_surface()
    source_mesh = tessellate(source_surface, N_U, N_V)
    return [
        rung_1_full_carry(source_mesh, source_surface),
        rung_2_estimated_uv(source_mesh, source_surface),
        rung_3_point_cloud(source_mesh, source_surface, seed=seed),
        rung_4_remesh(source_mesh, source_surface),
    ]


# --- the summary ----------------------------------------------------------


def summary_table(results: list[RungResult]) -> str:
    """The deliverable: a table of rung, carried information, drift, curvature, status.

    Absent measurements read as a dash, not zero. The blocked rung carries no number.
    """
    header = (
        f"{'rung':<5}{'carried information':<48}"
        f"{'drift max':>12}{'drift mean':>12}{'curv max':>11}  status"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        if r.drift is not None:
            dmax = f"{r.drift['max']:.3e}"
            dmean = f"{r.drift['mean']:.3e}"
        else:
            dmax = dmean = "-"
        cmax = f"{r.curvature['max']:.3e}" if r.curvature is not None else "-"
        carried = r.carried if len(r.carried) <= 47 else r.carried[:44] + "..."
        lines.append(
            f"{r.rung:<5}{carried:<48}{dmax:>12}{dmean:>12}{cmax:>11}  {r.status}"
        )
    return "\n".join(lines)


def plot_ladder(results: list[RungResult], path) -> "object":
    """Optional bonus: plot drift versus rung so the climb is visible.

    Reads the same RungResult list the table reads, so the two cannot diverge. The
    drift axis is log-scaled because the climb spans orders of magnitude. Rungs with
    no fit (blocked) are annotated on the axis rather than plotted as a point, since
    there is no number to plot. Headless (matplotlib Agg).
    """
    from pathlib import Path

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fitted = [r for r in results if r.drift is not None]
    rungs = [r.rung for r in fitted]
    dmax = [r.drift["max"] for r in fitted]
    dmean = [r.drift["mean"] for r in fitted]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(rungs, dmax, "o-", color="crimson", label="drift max")
    ax.plot(rungs, dmean, "s--", color="steelblue", label="drift mean")
    ax.set_yscale("log")
    ax.set_xlabel("rung (carried information removed left to right)")
    ax.set_ylabel("positional drift (mm, log scale)")
    ax.set_title("Drift climbs as carried information is removed")
    ax.set_xticks([r.rung for r in results])
    ax.set_xticklabels(
        [f"{r.rung}\n{r.name}" for r in results], fontsize=8
    )
    ax.legend()
    ax.grid(True, which="both", alpha=0.3)

    # Annotate any rung that produced no fit, since it has no point on the curve.
    blocked = [r for r in results if r.drift is None]
    if blocked and dmax:
        y = min(dmean)
        for r in blocked:
            ax.annotate(
                f"{r.status}\n(no fit)",
                xy=(r.rung, y),
                ha="center",
                va="bottom",
                fontsize=8,
                color="dimgray",
            )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path
