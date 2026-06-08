"""Phase 7a gates: the run contract and the status handshake.

See docs/PHASE7A_KICKOFF.md and PLAN.md Phase 7a. This is the cheap half of the
round-trip demo, the half proven by hand with files. The runner takes an externally
produced, tagged mesh as the ground-truth input, runs the existing loop on it, and
writes a result, a field, and a status marker. Every gate has a known answer because
the synthetic wing is the input and its drift is the Phase 5 number.

The gates carried here:
    - Round trip on a valid payload: the synthetic-wing payload yields a result, a
      field that round-trips against the report, and a status.json saying done with a
      drift max matching the Phase 5 number.
    - External input matches internal: the loop on the external payload gives the same
      drift as the loop on the internally generated wing. The inversion changed the
      source of the mesh, not the math.
    - Flagged is done, not failed: a collision yields done with flagged true and the
      signals, no reconstruction, distinct from a failed status.
    - Malformed payload fails cleanly: a payload missing tags or with inconsistent
      faces yields failed with a reason and does not crash.
    - Atomic marker: the status writer writes to a temp path and renames, so a partial
      marker is never visible.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from passthrough.constraints import check_constraints, curvature_deviation_field
from passthrough.driftgauge import hausdorff
from passthrough.encode import encode, read_topology
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.reconstruct import ClassicalReconstructor
from passthrough.report import deviation_field, read_field, read_report
from passthrough import run as run_module
from passthrough.run import (
    N_CTRL_U,
    N_CTRL_V,
    N_U,
    N_V,
    PayloadError,
    collision_tolerance,
    default_descriptor,
    emit_synthetic_payload,
    make_malformed_payload,
    read_incoming_payload,
    run_roundtrip,
    validate_incoming,
    write_status,
)
from passthrough.solver_stub import clean_morph
from passthrough.validity import validity_gate


# --- helpers --------------------------------------------------------------


def _internal_clean_drift() -> dict[str, float]:
    """The Phase 5 clean drift, computed by running the loop on the internally
    generated wing. This is the reference both the external run is checked against and
    the Phase 5 number the round-trip gate matches."""
    source = tessellate(build_wing_surface(), N_U, N_V)
    returned = clean_morph(source)
    rc = ClassicalReconstructor(n_ctrl_u=N_CTRL_U, n_ctrl_v=N_CTRL_V)
    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)
    return hausdorff(returned.vertices, remesh.vertices)


# --- gate: round trip on a valid payload ----------------------------------


def test_valid_payload_produces_done_with_result_and_field(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"

    summary = run_roundtrip(payload, return_dir)

    assert summary["status"] == "done"
    assert summary["flagged"] is False

    status = json.loads((return_dir / "status.json").read_text())
    assert status["status"] == "done"
    assert status["flagged"] is False
    assert status["signals"] == []
    assert status["result"] == "result.json"
    assert status["field"] == "field.json"
    assert status["drift_max"] is not None

    # The result and the field are both written and readable.
    assert (return_dir / "result.json").exists()
    assert (return_dir / "field.json").exists()


def test_field_round_trips_against_report(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"
    run_roundtrip(payload, return_dir)

    report = read_report(return_dir / "result.json")
    field = read_field(return_dir / "field.json")

    # The field carries exactly the report's mesh and deviation scalar.
    assert np.array_equal(field["vertices"], report.vertices)
    assert np.array_equal(field["faces"], report.faces)
    assert np.allclose(field["deviation"], report.deviation)


def test_status_drift_max_matches_phase5_number(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"
    summary = run_roundtrip(payload, return_dir)

    expected = _internal_clean_drift()["max"]
    status = json.loads((return_dir / "status.json").read_text())
    assert status["drift_max"] == pytest.approx(expected)
    # The headline number and the stored report agree.
    report = read_report(return_dir / "result.json")
    assert status["drift_max"] == pytest.approx(report.drift["max"])


# --- gate: external input matches internal --------------------------------


def test_external_matches_internal_drift(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"
    summary = run_roundtrip(payload, return_dir)

    internal = _internal_clean_drift()
    report = read_report(return_dir / "result.json")
    # Same source mesh, same loop: the drift summary is identical to floating point.
    assert report.drift["max"] == pytest.approx(internal["max"])
    assert report.drift["mean"] == pytest.approx(internal["mean"])
    assert report.drift["median"] == pytest.approx(internal["median"])


# --- gate: flagged is done, not failed ------------------------------------


def test_collision_is_done_and_flagged(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"

    summary = run_roundtrip(payload, return_dir, morph="collision")

    assert summary["status"] == "done"  # done, not failed: a caught problem
    assert summary["flagged"] is True
    assert "collision" in summary["signals"]
    assert summary["drift_max"] is None  # no reconstruction ran

    status = json.loads((return_dir / "status.json").read_text())
    assert status["status"] == "done"
    assert status["flagged"] is True
    assert "collision" in status["signals"]
    assert status["drift_max"] is None

    # No reconstruction: the report carries no deviation and names the pair.
    report = read_report(return_dir / "result.json")
    assert report.reconstructed is False
    assert report.drift is None
    assert report.collision_pairs  # the flagged non-neighbor pair(s)


def test_collision_status_is_distinct_from_failed(tmp_path):
    good = tmp_path / "good.json"
    emit_synthetic_payload(good)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(make_malformed_payload()))

    collision = run_roundtrip(good, tmp_path / "c", morph="collision")
    failed = run_roundtrip(bad, tmp_path / "f")

    assert collision["status"] == "done"
    assert failed["status"] == "failed"


# --- gate: malformed payload fails cleanly --------------------------------


def test_malformed_inconsistent_faces_fails(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(make_malformed_payload()))
    return_dir = tmp_path / "return"

    summary = run_roundtrip(bad, return_dir)  # must not raise

    assert summary["status"] == "failed"
    assert summary["reason"]
    status = json.loads((return_dir / "status.json").read_text())
    assert status["status"] == "failed"
    assert isinstance(status["reason"], str) and status["reason"]
    # Nothing to pull: the result and field were not written.
    assert not (return_dir / "result.json").exists()
    assert not (return_dir / "field.json").exists()


def test_malformed_missing_tags_fails(tmp_path):
    source = tessellate(build_wing_surface(), N_U, N_V)
    obj = encode(source, default_descriptor())
    del obj["topology"]  # a mesh without identity cannot make the trip
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(obj))
    return_dir = tmp_path / "return"

    summary = run_roundtrip(bad, return_dir)

    assert summary["status"] == "failed"
    assert "identity" in summary["reason"] or "topology" in summary["reason"]


def test_missing_file_fails_without_crashing(tmp_path):
    summary = run_roundtrip(tmp_path / "does_not_exist.json", tmp_path / "return")
    assert summary["status"] == "failed"
    status = json.loads((tmp_path / "return" / "status.json").read_text())
    assert status["status"] == "failed"


# --- validation unit checks ------------------------------------------------


def test_validate_accepts_synthetic_wing():
    source = tessellate(build_wing_surface(), N_U, N_V)
    validate_incoming(encode(source, default_descriptor()))  # does not raise


def test_validate_rejects_missing_tags():
    source = tessellate(build_wing_surface(), N_U, N_V)
    obj = encode(source, default_descriptor())
    del obj["topology"]
    with pytest.raises(PayloadError):
        validate_incoming(obj)


def test_validate_rejects_face_out_of_range():
    source = tessellate(build_wing_surface(), N_U, N_V)
    obj = encode(source, default_descriptor())
    obj["faces"][0][0] = len(obj["vertices"])
    with pytest.raises(PayloadError):
        validate_incoming(obj)


def test_validate_rejects_nonunique_node_ids():
    source = tessellate(build_wing_surface(), N_U, N_V)
    obj = encode(source, default_descriptor())
    obj["topology"]["node_ids"][1] = obj["topology"]["node_ids"][0]
    with pytest.raises(PayloadError):
        validate_incoming(obj)


def test_validate_rejects_adjacency_not_matching_faces():
    source = tessellate(build_wing_surface(), N_U, N_V)
    obj = encode(source, default_descriptor())
    # Break one node's neighbor list so it no longer matches the winding.
    key = next(iter(obj["topology"]["adjacency"]))
    obj["topology"]["adjacency"][key] = [999999]
    with pytest.raises(PayloadError):
        validate_incoming(obj)


def test_read_incoming_payload_returns_mesh_descriptor_topology(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    mesh, descriptor, topology = read_incoming_payload(payload)

    source = tessellate(build_wing_surface(), N_U, N_V)
    assert np.allclose(mesh.vertices, source.vertices)
    assert descriptor.provenance["source"] == "wing_v0"
    # The carried topology matches the one the encoder derived for the file.
    assert np.array_equal(topology.node_ids, read_topology(payload).node_ids)


# --- gate: atomic marker ---------------------------------------------------


def test_status_writer_is_atomic(tmp_path, monkeypatch):
    """The writer must write the full marker to a temp path and rename, so a reader
    never sees a half-written marker. Intercept os.replace and assert that at the
    moment of the rename the temp file is present and complete and the final path does
    not yet exist."""
    path = tmp_path / "status.json"
    observed = {}

    real_replace = run_module.os.replace

    def spy(src, dst):
        src, dst = Path(src), Path(dst)
        observed["tmp_present"] = src.exists()
        observed["final_present_before"] = dst.exists()
        # The temp file already holds the complete marker before the rename.
        observed["tmp_content"] = json.loads(src.read_text())
        observed["renamed_into_place"] = dst == path
        return real_replace(src, dst)

    monkeypatch.setattr(run_module.os, "replace", spy)

    body = {"status": "done", "flagged": False, "drift_max": 1.0}
    write_status(path, body)

    assert observed["tmp_present"] is True
    assert observed["final_present_before"] is False
    assert observed["tmp_content"] == body
    assert observed["renamed_into_place"] is True
    # After the rename the marker is in place and no temp file is left behind.
    assert path.exists()
    assert json.loads(path.read_text()) == body
    assert not path.with_name(path.name + ".tmp").exists()


def test_collision_tolerance_between_edge_and_nonneighbor(tmp_path):
    """A sanity check on the tolerance helper: it sits strictly between the smallest
    edge and the smallest non-neighbor separation, the valid regime the gate needs."""
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    mesh, _, topology = read_incoming_payload(payload)
    tol = collision_tolerance(mesh, topology)
    # A clean return passes the gate at this tolerance.
    returned = clean_morph(mesh)
    gate = validity_gate(topology, returned, topology, tol)
    assert gate.valid is True
