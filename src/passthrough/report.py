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
    deviation:       the per-vertex scalar, one per vertex, or None on a flagged pass.
    drift:           the max/mean/median summary, or None on a flagged pass.
    constraints:     the constraint results, empty on a flagged pass.
    validity:        the gate result. Always present.
    collision_pairs: flagged non-neighbor node-id pairs, read from the gate.
    folded_faces:    flagged face row indices, read from the gate.
    provenance:      source identity and iteration index, copied from the descriptor.

    Lengths are consistent by construction: when deviation is present it has one
    entry per vertex, asserted here.
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
) -> LoopReport:
    """Gather one loop pass into a report.

    display_mesh is the geometry the display shows: the reconstructed mesh on a valid
    pass, the returned mesh on a flagged pass. The flagged regions are read from the
    gate result, not passed in, so they cannot disagree with the validity it carries.
    node_ids default to vertex order when not supplied.
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


def report_to_dict(report: LoopReport) -> dict[str, Any]:
    """The report as a plain JSON-serializable dict. The inverse is report_from_dict."""
    return {
        "vertices": report.vertices.tolist(),
        "faces": report.faces.tolist(),
        "node_ids": report.node_ids.tolist(),
        "deviation": None if report.deviation is None else report.deviation.tolist(),
        "drift": report.drift,
        "constraints": [asdict(c) for c in report.constraints],
        "validity": _validity_to_dict(report.validity),
        "collision_pairs": [list(p) for p in report.collision_pairs],
        "folded_faces": list(report.folded_faces),
        "provenance": report.provenance,
    }


def report_from_dict(obj: dict[str, Any]) -> LoopReport:
    """Inverse of report_to_dict."""
    deviation = obj.get("deviation")
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


def build_render_spec(report: LoopReport, title: str | None = None) -> RenderSpec:
    """Turn a report into a RenderSpec: resolve the flagged node-id pairs and face
    indices into vertex coordinates the drawing can place directly.

    A collision pair (a, b) in node-id space becomes the segment between the two
    vertices those names sit on. A folded face row index becomes the polygon of its
    vertices. This is the pure step the render gate asserts: the overlay geometry is
    present and correct without rendering a pixel.
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
