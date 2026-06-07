"""Phase 5 gates: the report, the field file, and the static render.

See specs/spec-05-report-visual.md. This phase adds no measurement. It gathers what
the earlier phases compute into one report object for a single loop pass, emits the
field file the live Grasshopper display reads, and renders the same numbers
headlessly so a reviewer with no Rhino can look at them.

Every assertion has a known answer from synthetic ground truth: the solver morph
modes are deterministic stimuli (clean, collision, fold), each built to produce one
shape of loop pass.

This file carries the Phase 5 gates:
    - Report assembles. A completed pass produces a report carrying drift, residuals,
      validity, and a per-vertex deviation field, with one deviation per vertex.
    - Field file round-trips. The emitted file reads back with mesh and scalar
      matching the report.
    - Static render is produced. The render writes a non-empty PNG. On a flagged pass
      the flagged regions are present in the render spec.
    - The clean-vs-flagged distinction carries through: clean renders the deviation
      field with no flags, collision marks the pair, fold marks the faces.
    - One source of numbers. The deviation field is the directed form of the same
      computation the drift summary aggregates.
"""

from __future__ import annotations

import numpy as np
import pytest

from passthrough.constraints import check_constraints, curvature_deviation_field
from passthrough.driftgauge import hausdorff, nearest_distances
from passthrough.encode import (
    Descriptor,
    mesh_filename,
    read_payload,
    read_topology,
    write_payload,
)
from passthrough.geometry import Mesh, build_wing_surface, tessellate
from passthrough.reconstruct import ClassicalReconstructor
from passthrough.report import (
    LoopReport,
    assemble_report,
    build_comparison_spec,
    build_render_spec,
    deviation_field,
    field_to_dict,
    read_field,
    read_report,
    render,
    render_comparison,
    report_from_dict,
    report_to_dict,
    write_field,
    write_report,
)
from passthrough.solver_stub import solve_morph
from passthrough.validity import validity_gate

N_U, N_V = 24, 12


# --- fixtures: one loop pass per shape ------------------------------------


def _descriptor(index: int = 0) -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )


def _source_mesh() -> Mesh:
    return tessellate(build_wing_surface(), N_U, N_V)


def _tolerance(mesh, topology) -> float:
    """A collision tolerance between the smallest edge and the smallest non-neighbor
    separation, so a clean return has no non-neighbor pair within it. Same regime the
    Phase 4 tests use."""
    from scipy.spatial import cKDTree

    v, ids, adj = mesh.vertices, topology.node_ids, topology.adjacency
    tree = cKDTree(v)
    edge_min, nonneighbor_min = np.inf, np.inf
    for i, j in tree.query_pairs(r=0.5):
        a, b = int(ids[i]), int(ids[j])
        d = float(np.linalg.norm(v[i] - v[j]))
        if b in adj.get(a, ()):
            edge_min = min(edge_min, d)
        else:
            nonneighbor_min = min(nonneighbor_min, d)
    return float(np.sqrt(edge_min * nonneighbor_min))


def _clean_pass(out_dir, in_dir, index=0):
    """A valid loop pass: gate clears, reconstruction runs, drift and the deviation
    field exist. Returns the assembled report."""
    source = _source_mesh()
    write_payload(out_dir / mesh_filename(index), source, _descriptor(index))
    sent = read_topology(out_dir / mesh_filename(index))
    tol = _tolerance(source, sent)

    solve_morph(out_dir, in_dir, index, "clean")
    returned, desc = read_payload(in_dir / mesh_filename(index))
    returned_topo = read_topology(in_dir / mesh_filename(index))

    gate = validity_gate(sent, returned, returned_topo, tol)
    assert gate.reconstruct  # the clean pass clears the gate

    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)

    deviation = deviation_field(remesh.vertices, returned.vertices)
    drift = hausdorff(returned.vertices, remesh.vertices)
    source_surface = build_wing_surface()
    constraints = check_constraints(desc, recon, source=source_surface)
    curv = curvature_deviation_field(recon, source_surface)

    return assemble_report(
        remesh,
        gate,
        deviation=deviation,
        drift=drift,
        constraints=constraints,
        provenance=desc.provenance,
        curvature_deviation=curv["deviation"],
        curvature_points=curv["points"],
    ), returned, remesh


def _flagged_pass(out_dir, in_dir, morph, index=0):
    """A flagged loop pass (collision or fold): the gate stops, no reconstruction.
    The report is assembled on the returned mesh with the flags read from the gate."""
    source = _source_mesh()
    write_payload(out_dir / mesh_filename(index), source, _descriptor(index))
    sent = read_topology(out_dir / mesh_filename(index))
    tol = _tolerance(source, sent)

    solve_morph(out_dir, in_dir, index, morph)
    returned, desc = read_payload(in_dir / mesh_filename(index))
    returned_topo = read_topology(in_dir / mesh_filename(index))

    gate = validity_gate(sent, returned, returned_topo, tol)
    assert not gate.reconstruct  # the flagged pass stops the gate

    return assemble_report(
        returned,
        gate,
        node_ids=returned_topo.node_ids,
        provenance=desc.provenance,
    ), returned


# --- gate 1: report assembles ---------------------------------------------


def test_report_assembles_with_all_pieces(tmp_path):
    # A completed loop pass produces a report carrying the drift summary, the
    # constraint residuals, the validity result, and the per-vertex deviation field.
    report, returned, remesh = _clean_pass(tmp_path / "out", tmp_path / "in")

    assert report.drift is not None
    assert set(report.drift) == {"max", "mean", "median"}
    assert report.constraints  # at least the declared ruled constraint
    assert report.validity.valid
    assert report.deviation is not None
    assert report.reconstructed


def test_deviation_has_one_entry_per_vertex(tmp_path):
    # Array lengths are consistent: one deviation per displayed vertex.
    report, _, remesh = _clean_pass(tmp_path / "out", tmp_path / "in")
    assert report.deviation.shape[0] == report.vertices.shape[0]
    assert report.vertices.shape[0] == remesh.vertices.shape[0]
    assert report.node_ids.shape[0] == report.vertices.shape[0]


def test_deviation_length_mismatch_is_rejected():
    # The report refuses an inconsistent field: the lengths are a contract.
    verts = np.zeros((10, 3))
    faces = np.zeros((4, 4), dtype=int)
    from passthrough.validity import GateResult

    gate = GateResult(valid=True, reconstruct=True, results=[])
    with pytest.raises(ValueError):
        LoopReport(
            vertices=verts,
            faces=faces,
            node_ids=np.arange(10),
            validity=gate,
            deviation=np.zeros(9),  # one short
        )


def test_one_source_of_numbers(tmp_path):
    # The deviation field is the reconstruction-to-target direction of the same
    # nearest-distance computation the drift summary aggregates. Its maximum is one of
    # the two directed maxima the summary's symmetric maximum is taken over, so it is
    # bounded by it.
    report, returned, remesh = _clean_pass(tmp_path / "out", tmp_path / "in")
    direct = nearest_distances(remesh.vertices, returned.vertices)
    assert np.allclose(report.deviation, direct)
    assert report.deviation.max() <= report.drift["max"] + 1e-12


# --- gate 1b: a flagged pass carries no measurement -----------------------


def test_flagged_pass_has_no_drift_or_deviation(tmp_path):
    # A flagged pass stops the gate before reconstruction. The report carries no
    # invented drift number and no deviation field: absent measurements are absent.
    report, _ = _flagged_pass(tmp_path / "out", tmp_path / "in", "collision")
    assert report.drift is None
    assert report.deviation is None
    assert report.constraints == []
    assert not report.reconstructed
    assert not report.validity.valid


# --- gate 2: report and field file round-trip -----------------------------


def test_report_round_trips_through_json(tmp_path):
    # The arrays, the drift summary, the residuals, and the validity result all
    # survive a serialize/parse.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    back = report_from_dict(report_to_dict(report))

    assert np.allclose(back.vertices, report.vertices)
    assert np.array_equal(back.faces, report.faces)
    assert np.allclose(back.deviation, report.deviation)
    assert back.drift == report.drift
    assert back.validity.valid == report.validity.valid
    assert [c.residual for c in back.constraints] == [
        c.residual for c in report.constraints
    ]


def test_report_file_round_trips(tmp_path):
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    path = write_report(tmp_path / "report.json", report)
    back = read_report(path)
    assert np.allclose(back.deviation, report.deviation)
    assert back.drift == report.drift


def test_field_file_round_trips(tmp_path):
    # The emitted field file reads back with its mesh and scalar matching the report.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    path = write_field(tmp_path / "field.json", report)
    back = read_field(path)

    assert back["format"] == "passthrough.deviation_field.v1"
    assert np.allclose(back["vertices"], report.vertices)
    assert np.array_equal(back["faces"], report.faces)
    assert np.allclose(back["deviation"], report.deviation)
    # The range is for a stable color scale: it spans the scalar.
    lo, hi = back["deviation_range"]
    assert lo == pytest.approx(float(report.deviation.min()))
    assert hi == pytest.approx(float(report.deviation.max()))


def test_field_file_carries_flags_on_a_collision_pass(tmp_path):
    # A flagged pass field file carries the collision pair so the display can mark it,
    # and a null deviation (no reconstruction ran).
    report, _ = _flagged_pass(tmp_path / "out", tmp_path / "in", "collision")
    obj = field_to_dict(report)
    assert obj["deviation"] is None
    assert obj["deviation_range"] is None
    assert obj["flagged"]["collision_pairs"]  # the coinciding pair is named


# --- gate 3: the static render produces a PNG -----------------------------


def _png_is_valid(path) -> bool:
    """A non-empty file with the PNG magic header."""
    data = path.read_bytes()
    return len(data) > 0 and data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_writes_a_valid_png_on_a_clean_pass(tmp_path):
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    path = render(report, tmp_path / "clean.png")
    assert path.exists()
    assert _png_is_valid(path)


def test_render_writes_a_valid_png_on_a_flagged_pass(tmp_path):
    report, _ = _flagged_pass(tmp_path / "out", tmp_path / "in", "fold")
    path = render(report, tmp_path / "fold.png")
    assert path.exists()
    assert _png_is_valid(path)


# --- gate 4: the clean-vs-flagged distinction carries through -------------


def test_clean_render_spec_has_field_and_no_flags(tmp_path):
    # A clean pass renders the deviation field with no flagged regions.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    spec = build_render_spec(report)
    assert spec.deviation is not None
    assert spec.collision_segments == []
    assert spec.folded_polygons == []
    # The title carries the drift numbers so the image stands alone.
    assert "max" in spec.title and "mm" in spec.title


def test_collision_render_spec_marks_the_pair(tmp_path):
    # A collision pass renders with the collision pair marked: the overlay segment
    # joins the two coinciding vertices. Asserted on the spec since pixels cannot be.
    report, returned = _flagged_pass(tmp_path / "out", tmp_path / "in", "collision")
    spec = build_render_spec(report)
    assert spec.deviation is None
    assert spec.collision_segments  # at least one marked pair
    # The segment is two real vertices, and the pair coincided (separation ~ 0).
    seg = spec.collision_segments[0]
    assert seg.shape == (2, 3)
    assert np.linalg.norm(seg[0] - seg[1]) < 1e-6


def test_fold_render_spec_marks_the_faces(tmp_path):
    # A fold pass renders with the folded faces marked: each overlay polygon is the
    # vertex polygon of an inverted face row.
    report, returned = _flagged_pass(tmp_path / "out", tmp_path / "in", "fold")
    spec = build_render_spec(report)
    assert spec.deviation is None
    assert spec.folded_polygons  # at least one marked face
    poly = spec.folded_polygons[0]
    assert poly.ndim == 2 and poly.shape[1] == 3
    # The polygon vertices are the returned mesh vertices of the flagged face.
    f0 = report.folded_faces[0]
    assert np.allclose(poly, report.vertices[report.faces[f0]])


def test_collision_and_fold_titles_name_the_signal(tmp_path):
    # A flagged title names the gate signal and says no reconstruction ran, so the
    # fallback slide reads correctly without the live demo.
    report, _ = _flagged_pass(tmp_path / "out", tmp_path / "in", "collision")
    spec = build_render_spec(report)
    assert "collision" in spec.title
    assert "no reconstruction" in spec.title


# --- addendum gates: the two-panel comparison render ----------------------


def test_comparison_spec_assembles_two_panels(tmp_path):
    # Given a clean pass, the comparison spec carries a positional panel and a
    # curvature panel, each with a deviation field of consistent length: positional
    # one-per-vertex, curvature one-per-sample-point.
    report, _, remesh = _clean_pass(tmp_path / "out", tmp_path / "in")
    spec = build_comparison_spec(report)

    assert spec.positional.deviation is not None
    assert spec.curvature.deviation is not None
    assert spec.reconstructed

    # Each panel's field pairs with its own points.
    assert spec.positional.deviation.shape[0] == spec.positional.points.shape[0]
    assert spec.positional.points.shape[0] == remesh.vertices.shape[0]
    assert spec.curvature.deviation.shape[0] == spec.curvature.points.shape[0]


def test_comparison_render_writes_a_valid_png(tmp_path):
    # The two-panel render writes a non-empty PNG for a clean pass.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    path = render_comparison(report, tmp_path / "comparison.png")
    assert path.exists()
    assert _png_is_valid(path)


def test_comparison_fields_are_distinct_quantities(tmp_path):
    # The curvature panel is sourced from the curvature check, not a copy of the
    # positional field. Rebuild the pass inline so the reconstruction is in hand, then
    # confirm the report's curvature field is exactly what the curvature check produces.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    source = _source_mesh()
    write_payload(out_dir / mesh_filename(0), source, _descriptor(0))
    sent = read_topology(out_dir / mesh_filename(0))
    tol = _tolerance(source, sent)

    solve_morph(out_dir, in_dir, 0, "clean")
    returned, _ = read_payload(in_dir / mesh_filename(0))
    returned_topo = read_topology(in_dir / mesh_filename(0))
    gate = validity_gate(sent, returned, returned_topo, tol)

    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)
    source_surface = build_wing_surface()

    deviation = deviation_field(remesh.vertices, returned.vertices)
    curv = curvature_deviation_field(recon, source_surface)
    report = assemble_report(
        remesh,
        gate,
        deviation=deviation,
        drift=hausdorff(returned.vertices, remesh.vertices),
        curvature_deviation=curv["deviation"],
        curvature_points=curv["points"],
    )

    # The report's curvature field is the curvature check's output, not the positional.
    assert np.allclose(report.curvature_deviation, curv["deviation"])
    assert report.curvature_deviation.shape != report.deviation.shape
    # The thesis: positional drift is tiny while curvature deviation is large. The two
    # quantities are not interchangeable.
    assert report.deviation.max() < report.curvature_deviation.max()


def test_comparison_panels_have_separate_scales(tmp_path):
    # Each panel carries its own (min, max), so the render does not flatten one panel
    # onto the other's range. The two magnitudes differ by orders, so the scales differ.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    spec = build_comparison_spec(report)

    assert spec.positional.scale is not None
    assert spec.curvature.scale is not None
    assert spec.positional.scale != spec.curvature.scale

    pos = spec.positional
    cur = spec.curvature
    assert pos.scale == (float(pos.deviation.min()), float(pos.deviation.max()))
    assert cur.scale == (float(cur.deviation.min()), float(cur.deviation.max()))


def test_single_panel_render_spec_carries_no_curvature(tmp_path):
    # The single-panel render is unchanged: its spec is the positional field alone,
    # with no curvature attribute. The two-panel render is the new, separate path.
    report, _, _ = _clean_pass(tmp_path / "out", tmp_path / "in")
    spec = build_render_spec(report)
    assert spec.deviation is not None
    assert not hasattr(spec, "curvature")
    assert not hasattr(spec, "curvature_deviation")


def test_comparison_flagged_pass_is_gray_with_flag(tmp_path):
    # On a flagged pass there is no reconstruction and no deviation fields, so the
    # comparison shows the gray-with-flag state, not two empty panels. The collision
    # overlay is still present, and the render writes a valid PNG.
    report, _ = _flagged_pass(tmp_path / "out", tmp_path / "in", "collision")
    spec = build_comparison_spec(report)

    assert not spec.reconstructed
    assert spec.positional.deviation is None
    assert spec.curvature.deviation is None
    assert spec.positional.scale is None
    assert spec.curvature.scale is None
    assert spec.collision_segments  # the coinciding pair is still marked

    path = render_comparison(report, tmp_path / "collision_comparison.png")
    assert path.exists()
    assert _png_is_valid(path)
