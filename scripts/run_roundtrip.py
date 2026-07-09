"""Run the Phase 7a round trip by hand: a payload in, a result and a status marker out.

This is step 2 of the three-step file demo, the half you can prove by hand before any
C# or Rhino exists. See docs/PHASE7A_KICKOFF.md.

Emit a known-good payload, run it, and watch the runner write a result, a field, and a
status.json saying done:

    python scripts/run_roundtrip.py emit /tmp/incoming.json
    python scripts/run_roundtrip.py run /tmp/incoming.json /tmp/return

Then emit a deliberately malformed payload and watch the runner write a status.json
saying failed with a reason, without crashing:

    python scripts/run_roundtrip.py emit /tmp/bad.json --kind malformed
    python scripts/run_roundtrip.py run /tmp/bad.json /tmp/return_bad

The collision morph stands in for an external solve that returns a self-intersecting
mesh. It is a done with flagged true, not a failed: a caught problem is a successful
run that found one.

    python scripts/run_roundtrip.py run /tmp/incoming.json /tmp/return_c --morph collision
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.run import (  # noqa: E402
    emit_synthetic_payload,
    run_roundtrip,
    write_malformed_payload,
)


def _cmd_emit(args: argparse.Namespace) -> int:
    if args.kind == "good":
        path = emit_synthetic_payload(args.path)
        print(f"wrote a valid incoming payload (synthetic wing) to {path}")
    else:
        path = write_malformed_payload(args.path)
        print(f"wrote a malformed incoming payload (face past the mesh) to {path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    summary = run_roundtrip(args.payload, args.return_dir, morph=args.morph)

    print(f"status: {summary['status']}")
    if summary["status"] == "failed":
        print(f"  reason: {summary['reason']}")
        print(f"  wrote:  {summary['status_path']}")
        return 0

    flag = "flagged" if summary["flagged"] else "clean"
    print(f"  pass:    {flag}")
    print(f"  signals: {summary['signals']}")
    if summary["drift_max"] is not None:
        print(f"  drift max: {summary['drift_max']:.5e}")
    else:
        print("  drift max: none (no reconstruction on a flagged pass)")
    if summary["curvature_max"] is not None:
        print(f"  curvature max: {summary['curvature_max']:.5e}")
    else:
        print("  curvature max: none (no reconstruction on a flagged pass)")
    if summary["surface_written"]:
        print("  surface: written to result.json (passthrough.surface.v1), the")
        print("           reconstructed analytic surface for the bench to rebuild")
    else:
        print("  surface: none (no reconstruction on a flagged pass)")
    print("  wrote:")
    for p in summary["wrote"]:
        print(f"    {p}")

    # Echo the status marker, the contract the import side reads.
    with Path(summary["status_path"]).open("r", encoding="utf-8") as fh:
        print("  status.json:")
        for line in json.dumps(json.load(fh), indent=2).splitlines():
            print(f"    {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 7a round-trip runner. Emit a payload, or run one."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit", help="write an incoming payload to feed the runner")
    emit.add_argument("path", help="where to write the payload JSON")
    emit.add_argument(
        "--kind",
        choices=("good", "malformed"),
        default="good",
        help="good is the synthetic wing; malformed has a face past the mesh",
    )
    emit.set_defaults(func=_cmd_emit)

    run = sub.add_parser("run", help="run the loop on a payload and write the result")
    run.add_argument("payload", help="path to the incoming payload JSON")
    run.add_argument("return_dir", help="folder to write result, field, and status into")
    run.add_argument(
        "--morph",
        choices=("clean", "collision", "fold", "ffd"),
        default="clean",
        help="the synthetic solver step, standing in for an external solve",
    )
    run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
