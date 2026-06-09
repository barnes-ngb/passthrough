"""The reporting layer: assemble one loop pass, emit a field file, render a PNG.

See specs/spec-05-report-visual.md. This module adds no measurement. driftgauge
already produces the drift summary and the per-vertex deviation; constraints.py
produces the residuals; validity.py produces the gate result with its flagged node
pairs and faces. This phase gathers what already exists into one report object,
serializes it, emits the file the live Grasshopper display reads and colors, and
draws the same numbers headlessly so a reviewer with no Rhino can look at them.

Honesty category 1: this is instrument logic, authored here.

The boundary that must not move (AGENT.md): math stays in tested Python. The
deviation field reuses driftgauge.nearest_distances; nothing is recomputed in a
display layer. The Grasshopper coloring is not built here. This module emits the
file Grasshopper reads, and the static render reads the same report, so the two
display paths draw one set of numbers and cannot diverge.

Two shapes of loop pass are carried honestly:
    - A valid pass. The gate cleared, the reconstruction ran. There is a drift
      summary, a residual per declared constraint, and a per-vertex deviation field
      on the reconstructed mesh. Nothing is flagged.
    - A flagged pass. The gate stopped (collision or fold), so no reconstruction
      ran. There is no drift, no residual, no deviation field. There is a validity
      result naming the flagged pair or faces, carried on the returned mesh so the
      display can mark them.

A flagged pass does not get an invented drift number. Absent measurements are
absent, not zero.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from passthrough.constraints import ConstraintResult
from passthrough.driftgauge import nearest_distances
from passthrough.geometry import Mesh
from passthrough.validity import GateResult, ValidityResult

FIELD_FORMAT = "passthrough.deviation_field.v1"


# --- the deviation field --------------------------------------------------


def deviation_field(reconstructed: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Per reconstructed-vertex nearest distance to the target point set.

    This is the per-vertex form of what the drift meter aggregates. It reuses
    driftgauge.nearest_distances so the field and the drift summary read one
    computation: the field's maximum equals the directed reconstruction-to-target
    maximum the summary reports. One scalar per reconstructed vertex.
    """
    return nearest_distances(reconstructed, target)


def _flagged_regions(validity: GateResult) -> tuple[list, list]:
    """Read the flagged collision pairs and folded faces out of the gate result.

    The collision check reports offending non-neighbor node-id pairs in its
    node_ids; the fold check reports inverted face row indices in its faces. The
    gate stops at identity, so a failed-identity pass carries neither and the
    display has nothing geometric to mark.
    """
    collision_pairs: list = []
    folded_faces: list = []
    for r in validity.results:
        if r.signal == "collision":
            collision_pairs = [tuple(int(x) for x in pair) for pair in r.node_ids]
        elif r.signal == "fold":
            folded_faces = [int(f) for f in r.faces]
    return collision_pairs, folded_faces


# --- the report object ----------------------------------------------------


@dataclass
class LoopReport:
    """Everything one loop pass yields, gathered for display.

    vertices, faces: the displayed mesh. The reconstructed, re-tessellated mesh on a
        valid pass; the returned (deformed) mesh on a flagged pass.
    node_ids:        one name per displayed vertex, so a flagged node-id pair maps to
        vertex rows for the display.
    deviation:       the per-vertex positional scalar, one per vertex, or None on a
        flagged pass.
    drift:           the max/mean/median summary, or None on a flagged pass.
    constraints:     the constraint results, empty on a flagged pass.
    validity:        the gate result. Always present.
    collision_pairs: flagged non-neighbor node-id pairs, read from the gate.
    folded_faces:    flagged face row indices, read from the gate.
    provenance:      source identity and iteration index, copied from the descriptor.
    surface:         the reconstructed analytic surface in the spec-07-surface form
        (control net, weights, degrees, full knot vectors), or None on a flagged pass.
        This is what closes the round trip: the bench rebuilds it as a Rhino surface
        and sets it beside the original. It is data, not geometry math: the fit
        happened in the reconstructor, and this carries its output verbatim.
    curvature_deviation: the per-sample curvature scalar (|H_recon - H_source|), or
        None on a flagged pass. A second, distinct deviation field: positional drift
        is tiny while curvature deviation is large and concentrated at the leading
        edge. The comparison render draws the two side by side. Sampled on the
        curvature grid, so it is not one-per-vertex; it pairs with curvature_points.
    curvature_points: the (M, 3) sample positions the curvature field sits on, or
        None. One row per curvature_deviation entry.

    Lengths are consistent by construction: when deviation is present it has one
    entry per vertex, and curvature_deviation pairs with curvature_points, asserted
    here.
    """

    vertices: np.ndarray
    faces: np.ndarray
    node_ids: np.ndarray
    validity: GateResult
    deviation: np.ndarray | None = None
    drift: dict[str, float] | None = None
    constraints: list[ConstraintResult] = field(default_factory=list)
    collision_pairs: list = field(default_factory=list)
    folded_faces: list = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    surface: dict[str, Any] | None = None
    curvature_deviation: np.ndarray | None = None
    curvature_points: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float)
        self.faces = np.asarray(self.faces, dtype=int)
        self.node_ids = np.asarray(self.node_ids, dtype=int)
        if self.deviation is not None:
            self.deviation = np.asarray(self.deviation, dtype=float)
            if self.deviation.shape[0] != self.vertices.shape[0]:
                raise ValueError(
                    "deviation must have one entry per vertex "
                    f"({self.deviation.shape[0]} != {self.vertices.shape[0]})"
                )
        if self.node_ids.shape[0] != self.vertices.shape[0]:
            raise ValueError("node_ids must have one entry per vertex")
        if self.curvature_deviation is not None:
            self.curvature_deviation = np.asarray(self.curvature_deviation, dtype=float)
            if self.curvature_points is None:
                raise ValueError("curvature_deviation requires curvature_points")
            self.curvature_points = np.asarray(self.curvature_points, dtype=float)
            if self.curvature_points.shape[0] != self.curvature_deviation.shape[0]:
                raise ValueError(
                    "curvature_points must have one row per curvature_deviation entry "
                    f"({self.curvature_points.shape[0]} != "
                    f"{self.curvature_deviation.shape[0]})"
                )

    @property
    def reconstructed(self) -> bool:
        """True when the gate cleared and the reconstruction ran, so drift and the
        deviation field are present. False on a flagged pass."""
        return self.deviation is not None


def assemble_report(
    display_mesh: Mesh,
    validity: GateResult,
    node_ids: np.ndarray | None = None,
    deviation: np.ndarray | None = None,
    drift: dict[str, float] | None = None,
    constraints: list[ConstraintResult] | None = None,
    provenance: dict[str, Any] | None = None,
    surface: dict[str, Any] | None = None,
    curvature_deviation: np.ndarray | None = None,
    curvature_points: np.ndarray | None = None,
) -> LoopReport:
    """Gather one loop pass into a report.

    display_mesh is the geometry the display shows: the reconstructed mesh on a valid
    pass, the returned mesh on a flagged pass. The flagged regions are read from the
    gate result, not passed in, so they cannot disagree with the validity it carries.
    node_ids default to vertex order when not supplied. The curvature deviation field
    and its sample points are passed for the comparison render; both are absent on a
    flagged pass.
    """
    n = display_mesh.vertices.shape[0]
    ids = np.arange(n, dtype=int) if node_ids is None else np.asarray(node_ids, dtype=int)
    collision_pairs, folded_faces = _flagged_regions(validity)
    return LoopReport(
        vertices=display_mesh.vertices,
        faces=display_mesh.faces,
        node_ids=ids,
        validity=validity,
        deviation=deviation,
        drift=drift,
        constraints=list(constraints) if constraints else [],
        collision_pairs=collision_pairs,
        folded_faces=folded_faces,
        provenance=dict(provenance) if provenance else {},
        surface=surface,
        curvature_deviation=curvature_deviation,
        curvature_points=curvature_points,
    )


# --- report serialization -------------------------------------------------


def _validity_to_dict(gate: GateResult) -> dict[str, Any]:
    return {
        "valid": bool(gate.valid),
        "reconstruct": bool(gate.reconstruct),
        "signals": list(gate.signals),
        "results": [asdict(r) for r in gate.results],
    }


def _validity_from_dict(obj: dict[str, Any]) -> GateResult:
    results = [
        ValidityResult(
            check=r["check"],
            passed=bool(r["passed"]),
            signal=r["signal"],
            node_ids=[tuple(x) if isinstance(x, list) else x for x in r.get("node_ids", [])],
            faces=list(r.get("faces", [])),
            detail=dict(r.get("detail", {})),
        )
        for r in obj["results"]
    ]
    return GateResult(
        valid=bool(obj["valid"]),
        reconstruct=bool(obj["reconstruct"]),
        results=results,
    )


def _evaluation(report: LoopReport) -> dict[str, Any]:
    """The headline evaluation, surfaced at the top of the result so the bench reads
    the numbers without recomputing or parsing the full fields.

    Nothing new is measured here. The positional drift summary is driftgauge's, copied
    through. The curvature residual is the maximum of the curvature deviation field the
    constraint check already produced. Both are None on a flagged pass, where no
    reconstruction ran. These are the two numbers the round trip is judged on: how far
    the reconstruction drifted in position, and where its curvature broke.
    """
    curvature_max = (
        None
        if report.curvature_deviation is None
        else float(report.curvature_deviation.max())
    )
    return {"drift": report.drift, "curvature_max": curvature_max}


def report_to_dict(report: LoopReport) -> dict[str, Any]:
    """The report as a plain JSON-serializable dict. The inverse is report_from_dict.

    surface and evaluation are present on a reconstructed pass and absent (null) on a
    flagged one: a flagged pass has no analytic reconstruction to carry and no drift or
    curvature to headline.
    """
    return {
        "vertices": report.vertices.tolist(),
        "faces": report.faces.tolist(),
        "node_ids": report.node_ids.tolist(),
        "deviation": None if report.deviation is None else report.deviation.tolist(),
        "drift": report.drift,
        "evaluation": _evaluation(report),
        "surface": report.surface,
        "constraints": [asdict(c) for c in report.constraints],
        "validity": _validity_to_dict(report.validity),
        "collision_pairs": [list(p) for p in report.collision_pairs],
        "folded_faces": list(report.folded_faces),
        "provenance": report.provenance,
        "curvature_deviation": (
            None
            if report.curvature_deviation is None
            else report.curvature_deviation.tolist()
        ),
        "curvature_points": (
            None if report.curvature_points is None else report.curvature_points.tolist()
        ),
    }


def report_from_dict(obj: dict[str, Any]) -> LoopReport:
    """Inverse of report_to_dict."""
    deviation = obj.get("deviation")
    curv_dev = obj.get("curvature_deviation")
    curv_pts = obj.get("curvature_points")
    constraints = [
        ConstraintResult(
            type=c["type"],
            tolerance=float(c["tolerance"]),
            residual=float(c["residual"]),
            passed=bool(c["passed"]),
            detail=dict(c.get("detail", {})),
        )
        for c in obj.get("constraints", [])
    ]
    return LoopReport(
        vertices=np.asarray(obj["vertices"], dtype=float),
        faces=np.asarray(obj["faces"], dtype=int),
        node_ids=np.asarray(obj["node_ids"], dtype=int),
        validity=_validity_from_dict(obj["validity"]),
        deviation=None if deviation is None else np.asarray(deviation, dtype=float),
        drift=obj.get("drift"),
        constraints=constraints,
        collision_pairs=[tuple(p) for p in obj.get("collision_pairs", [])],
        folded_faces=list(obj.get("folded_faces", [])),
        provenance=dict(obj.get("provenance", {})),
        surface=obj.get("surface"),
        curvature_deviation=None if curv_dev is None else np.asarray(curv_dev, dtype=float),
        curvature_points=None if curv_pts is None else np.asarray(curv_pts, dtype=float),
    )


def write_report(path: str | Path, report: LoopReport) -> Path:
    """Serialize the report to JSON at path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report_to_dict(report), fh, indent=2)
    return path


def read_report(path: str | Path) -> LoopReport:
    """Read a serialized report back."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return report_from_dict(json.load(fh))


# --- the field file the live Grasshopper display reads --------------------


def field_to_dict(report: LoopReport) -> dict[str, Any]:
    """The deviation field as the documented Grasshopper field-file shape.

    A display artifact, not part of the exchange contract: it flows one way, from the
    instrument to the display. It carries the displayed mesh, the per-vertex scalar,
    a range for a stable color scale, and the flagged lists so the display can
    highlight the collision pair and the folded faces. See spec-05.
    """
    dev = report.deviation
    return {
        "format": FIELD_FORMAT,
        "units": "mm",
        "vertices": report.vertices.tolist(),
        "faces": report.faces.tolist(),
        "node_ids": report.node_ids.tolist(),
        "deviation": None if dev is None else dev.tolist(),
        "deviation_range": (
            None if dev is None else [float(dev.min()), float(dev.max())]
        ),
        "flagged": {
            "collision_pairs": [list(p) for p in report.collision_pairs],
            "folded_faces": list(report.folded_faces),
        },
    }


def write_field(path: str | Path, report: LoopReport) -> Path:
    """Emit the deviation field file Grasshopper reads and colors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(field_to_dict(report), fh, indent=2)
    return path


def read_field(path: str | Path) -> dict[str, Any]:
    """Read the field file back as its documented dict shape, arrays as numpy.

    Returns the same keys field_to_dict wrote, with vertices, faces, node_ids, and
    deviation converted to numpy arrays so a caller can compare them against a report.
    """
    with Path(path).open("r", encoding="utf-8") as fh:
        obj = json.load(fh)
    out: dict[str, Any] = dict(obj)
    out["vertices"] = np.asarray(obj["vertices"], dtype=float)
    out["faces"] = np.asarray(obj["faces"], dtype=int)
    out["node_ids"] = np.asarray(obj["node_ids"], dtype=int)
    if obj.get("deviation") is not None:
        out["deviation"] = np.asarray(obj["deviation"], dtype=float)
    return out


# --- the static render ----------------------------------------------------


@dataclass
class RenderSpec:
    """The pure inputs the drawing consumes, separated from matplotlib so the
    overlays can be tested without inspecting pixels.

    vertices, faces:    the displayed mesh.
    deviation:          the per-vertex scalar, or None on a flagged pass.
    collision_segments: each (2, 3): the two coinciding vertices of a flagged pair.
    folded_polygons:    each (K, 3): the vertex-coordinate polygon of an inverted face.
    drift:              the summary, for the title, or None.
    title:              the rendered title, carrying the numbers so the image stands
                        alone.
    """

    vertices: np.ndarray
    faces: np.ndarray
    deviation: np.ndarray | None
    collision_segments: list[np.ndarray]
    folded_polygons: list[np.ndarray]
    drift: dict[str, float] | None
    title: str


def _overlay_geometry(
    report: LoopReport,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Resolve the flagged node-id pairs and face indices into vertex coordinates.

    A collision pair (a, b) in node-id space becomes the segment between the two
    vertices those names sit on. A folded face row index becomes the polygon of its
    vertices. Shared by the single-panel and comparison specs so the overlays cannot
    diverge between the two render paths.
    """
    verts = report.vertices
    id_to_row = {int(nid): row for row, nid in enumerate(report.node_ids)}

    collision_segments: list[np.ndarray] = []
    for a, b in report.collision_pairs:
        ra, rb = id_to_row.get(int(a)), id_to_row.get(int(b))
        if ra is None or rb is None:
            continue
        collision_segments.append(np.array([verts[ra], verts[rb]]))

    folded_polygons: list[np.ndarray] = []
    for f in report.folded_faces:
        if 0 <= int(f) < report.faces.shape[0]:
            folded_polygons.append(verts[report.faces[int(f)]])

    return collision_segments, folded_polygons


def build_render_spec(report: LoopReport, title: str | None = None) -> RenderSpec:
    """Turn a report into a RenderSpec: resolve the flagged node-id pairs and face
    indices into vertex coordinates the drawing can place directly.

    A collision pair (a, b) in node-id space becomes the segment between the two
    vertices those names sit on. A folded face row index becomes the polygon of its
    vertices. This is the pure step the render gate asserts: the overlay geometry is
    present and correct without rendering a pixel.
    """
    verts = report.vertices
    collision_segments, folded_polygons = _overlay_geometry(report)

    if title is None:
        title = _default_title(report)

    return RenderSpec(
        vertices=verts,
        faces=report.faces,
        deviation=report.deviation,
        collision_segments=collision_segments,
        folded_polygons=folded_polygons,
        drift=report.drift,
        title=title,
    )


def _default_title(report: LoopReport) -> str:
    """A title that carries the pass's numbers so the image stands on its own."""
    iteration = report.provenance.get("iteration", "?")
    if report.reconstructed and report.drift is not None:
        d = report.drift
        return (
            f"deviation field (iter {iteration}): "
            f"max {d['max']:.3e}, mean {d['mean']:.3e}, median {d['median']:.3e} mm"
        )
    signals = ", ".join(report.validity.signals) or "flagged"
    return f"validity gate stopped (iter {iteration}): {signals}; no reconstruction"


def render(report: LoopReport, path: str | Path, title: str | None = None) -> Path:
    """Render the deviation field as color over the geometry and save a PNG.

    The Agg backend is set explicitly so nothing tries to open a window: this runs
    headless and is the slides-only fallback if the live demo fails. On a valid pass
    the mesh is colored by deviation with a colorbar. The flagged regions are
    overlaid: a collision pair is marked at both coinciding vertices with a
    connecting segment, a folded face as a highlighted polygon. The drift summary is
    written into the title so the image carries its own numbers.

    On a flagged pass deviation is None: the geometry is drawn in a neutral fill and
    the flagged regions are still marked.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    spec = build_render_spec(report, title=title)
    verts = spec.vertices

    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(111, projection="3d")

    if spec.deviation is not None:
        sc = ax.scatter(
            verts[:, 0],
            verts[:, 1],
            verts[:, 2],
            c=spec.deviation,
            cmap="viridis",
            s=14,
            depthshade=False,
        )
        cbar = fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.1)
        cbar.set_label("deviation (mm)")
    else:
        ax.scatter(
            verts[:, 0],
            verts[:, 1],
            verts[:, 2],
            color="0.7",
            s=10,
            depthshade=False,
        )

    # Folded faces: highlighted polygons.
    if spec.folded_polygons:
        poly = Poly3DCollection(
            spec.folded_polygons,
            facecolors="crimson",
            edgecolors="darkred",
            alpha=0.7,
        )
        ax.add_collection3d(poly)

    # Collision pairs: mark both coinciding vertices and connect them.
    for seg in spec.collision_segments:
        ax.plot(
            seg[:, 0],
            seg[:, 1],
            seg[:, 2],
            color="crimson",
            linewidth=2.0,
            marker="o",
            markersize=8,
        )

    ax.set_title(spec.title)
    ax.set_xlabel("x (chord)")
    ax.set_ylabel("y (span)")
    ax.set_zlabel("z (thickness)")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


# --- the two-panel comparison render --------------------------------------


@dataclass
class PanelSpec:
    """One panel of the comparison render: the points to color, their scalar, and the
    panel's own color scale.

    Each panel carries its own (min, max). The two are not flattened onto a shared
    range: positional drift is tiny while curvature deviation is large, and a shared
    scale would hide the contrast the comparison exists to show.

    points:    the (N, 3) positions to color.
    deviation: the per-point scalar, or None on a flagged pass.
    scale:     (min, max) of the scalar, this panel's own color range, or None.
    label:     the colorbar label naming the quantity and its units.
    """

    points: np.ndarray
    deviation: np.ndarray | None
    scale: tuple[float, float] | None
    label: str


@dataclass
class ComparisonSpec:
    """The two-panel render's pure inputs: positional deviation beside curvature
    deviation for the same reconstruction, plus the flagged overlays.

    positional:    left panel, positional deviation over the reconstructed mesh.
    curvature:     right panel, curvature deviation over the curvature sample points.
    faces:         the displayed mesh faces, for the folded-face overlay.
    collision_segments, folded_polygons: the flagged overlays, as in RenderSpec.
    drift:         the drift summary, for the title.
    title:         the rendered title, carrying the numbers so the image stands alone.
    reconstructed: True on a valid pass (both panels carry data); False on a flagged
                   pass (no panels of data: a single gray-with-flag view is drawn).
    """

    positional: PanelSpec
    curvature: PanelSpec
    faces: np.ndarray
    collision_segments: list[np.ndarray]
    folded_polygons: list[np.ndarray]
    drift: dict[str, float] | None
    title: str
    reconstructed: bool


def _comparison_title(report: LoopReport) -> str:
    """A title carrying both panels' headline numbers so the image stands on its own."""
    iteration = report.provenance.get("iteration", "?")
    if report.reconstructed and report.drift is not None:
        pos_max = report.drift["max"]
        body = f"positional drift max {pos_max:.3e} mm"
        if report.curvature_deviation is not None:
            cur_max = float(report.curvature_deviation.max())
            body += f"  |  curvature deviation max {cur_max:.3e} 1/mm"
        return f"positional vs curvature (iter {iteration}): {body}"
    signals = ", ".join(report.validity.signals) or "flagged"
    return f"validity gate stopped (iter {iteration}): {signals}; no reconstruction"


def build_comparison_spec(report: LoopReport, title: str | None = None) -> ComparisonSpec:
    """Turn a report into a ComparisonSpec: the positional panel beside the curvature
    panel, each with its own color scale, and the flagged overlays resolved to
    coordinates.

    The two panels are distinct quantities, not one field drawn twice. The positional
    panel is the per-vertex drift on the reconstructed mesh; the curvature panel is the
    per-sample |H_recon - H_source| field on the curvature sample points, sourced from
    the curvature check. Each panel's scale spans its own values, so the render does
    not flatten one onto the other's range. This is the pure step the gate asserts.
    """
    pos_dev = report.deviation
    pos_scale = (
        None if pos_dev is None else (float(pos_dev.min()), float(pos_dev.max()))
    )
    positional = PanelSpec(
        points=report.vertices,
        deviation=pos_dev,
        scale=pos_scale,
        label="positional deviation (mm)",
    )

    cur_dev = report.curvature_deviation
    cur_pts = report.curvature_points if cur_dev is not None else report.vertices
    cur_scale = (
        None if cur_dev is None else (float(cur_dev.min()), float(cur_dev.max()))
    )
    curvature = PanelSpec(
        points=cur_pts,
        deviation=cur_dev,
        scale=cur_scale,
        label="curvature deviation (1/mm)",
    )

    collision_segments, folded_polygons = _overlay_geometry(report)

    if title is None:
        title = _comparison_title(report)

    return ComparisonSpec(
        positional=positional,
        curvature=curvature,
        faces=report.faces,
        collision_segments=collision_segments,
        folded_polygons=folded_polygons,
        drift=report.drift,
        title=title,
        reconstructed=report.reconstructed,
    )


def render_comparison(
    report: LoopReport, path: str | Path, title: str | None = None
) -> Path:
    """Render positional deviation beside curvature deviation and save a PNG.

    Two panels of the same reconstruction: positional drift on the left, curvature
    deviation on the right. Each panel uses its own color scale, since the magnitudes
    differ by orders of magnitude and a shared scale would flatten one onto the other.
    The left panel reads calm; the right panel lights up at the leading edge. That
    contrast is the argument.

    On a flagged pass there is no reconstruction and no deviation fields, so this draws
    a single gray-with-flag view, not two empty panels: the same fallback the
    single-panel render shows.

    The Agg backend is set explicitly so nothing opens a window: this runs headless.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    spec = build_comparison_spec(report, title=title)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def label_axes(ax) -> None:
        ax.set_xlabel("x (chord)")
        ax.set_ylabel("y (span)")
        ax.set_zlabel("z (thickness)")

    def draw_overlays(ax) -> None:
        if spec.folded_polygons:
            poly = Poly3DCollection(
                spec.folded_polygons,
                facecolors="crimson",
                edgecolors="darkred",
                alpha=0.7,
            )
            ax.add_collection3d(poly)
        for seg in spec.collision_segments:
            ax.plot(
                seg[:, 0],
                seg[:, 1],
                seg[:, 2],
                color="crimson",
                linewidth=2.0,
                marker="o",
                markersize=8,
            )

    if not spec.reconstructed:
        # No reconstruction ran: one gray-with-flag view, not two empty panels.
        fig = plt.figure(figsize=(9, 6))
        ax = fig.add_subplot(111, projection="3d")
        pts = spec.positional.points
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], color="0.7", s=10, depthshade=False)
        draw_overlays(ax)
        ax.set_title(spec.title)
        label_axes(ax)
        fig.savefig(path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return path

    fig = plt.figure(figsize=(16, 6))
    panels = [(spec.positional, "viridis"), (spec.curvature, "magma")]
    for col, (panel, cmap) in enumerate(panels, start=1):
        ax = fig.add_subplot(1, 2, col, projection="3d")
        pts = panel.points
        vmin, vmax = panel.scale
        sc = ax.scatter(
            pts[:, 0],
            pts[:, 1],
            pts[:, 2],
            c=panel.deviation,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            s=14,
            depthshade=False,
        )
        cbar = fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.1)
        cbar.set_label(panel.label)
        ax.set_title(panel.label.split(" (")[0])
        label_axes(ax)

    fig.suptitle(spec.title)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path
