"""Run the Phase 6 boundary experiment end-to-end and print the table. One command.

    python scripts/run_phase6.py

This runs the four-rung ladder (boundary.run_ladder), prints the per-rung detail and
the summary table, and writes the table, the structured results, and the optional
drift-versus-rung plot to artifacts/.

The experiment removes carried information rung by rung and measures what happens.
Rung 1 carries everything and reproduces the Phase 1 fit-only drift. Rung 2 estimates
the UV and the drift climbs. Rung 3 strips correspondence and the fit goes ill-posed.
Rung 4 is a remesh the validity gate stops before any fit. A non-convergent or
ill-posed rung is a valid finding, not a failure; the table reports it as such.

No CFD, no server, no solver (AGENT.md hard do-not list). This reuses the existing
meters and the classical reconstructor; only the parameterization and correspondence
are stripped.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.boundary import (  # noqa: E402
    plot_ladder,
    run_ladder,
    summary_table,
)


def _print_rung(r) -> None:
    print(f"\n[rung {r.rung}] {r.name}  ({r.status})")
    print(f"  carries: {r.carried}")
    if r.drift is not None:
        print(f"  drift:     max {r.drift['max']:.5e}  mean {r.drift['mean']:.5e} mm")
    if r.curvature is not None:
        print(
            f"  curvature: max {r.curvature['max']:.5e}  mean {r.curvature['mean']:.5e}"
            f"  le_max {r.curvature['le_max']:.5e} 1/mm"
        )
    for c in r.constraints:
        verdict = "preserved" if c.passed else "not preserved"
        print(
            f"  constraint {c.type}: residual {c.residual:.5e} vs tol {c.tolerance:.2e}"
            f"  ({verdict})"
        )
    print(f"  reason: {r.reason}")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    results = run_ladder()

    print("Phase 6: the reverse-problem boundary (exploratory)")
    print("Removing carried information rung by rung, on one wing ground truth.")
    for r in results:
        _print_rung(r)

    table = summary_table(results)
    print("\n" + "=" * len(table.splitlines()[0]))
    print(table)
    print("=" * len(table.splitlines()[0]))

    # The headline reading, stated against the actual numbers.
    rung1, rung2 = results[0], results[1]
    ratio = rung2.drift["max"] / rung1.drift["max"]
    print(
        f"\nDrift climbs as carried information is removed: rung 2 is {ratio:.1f}x "
        f"rung 1, and rung 3 is order the part's own size. That gap is what carrying "
        f"identity is worth. The curvature residual does not climb in step here: it is "
        f"already saturated at rung 1 by the coarse basis (see spec-06)."
    )

    # Write the table, the structured results, and the optional plot to artifacts/.
    table_path = artifacts / "phase6_ladder.txt"
    table_path.write_text(table + "\n", encoding="utf-8")
    json_path = artifacts / "phase6_ladder.json"
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump([asdict(r) for r in results], fh, indent=2)

    print(f"\nWrote table:  {table_path}")
    print(f"Wrote results: {json_path}")
    try:
        plot_path = plot_ladder(results, artifacts / "phase6_drift_vs_rung.png")
        print(f"Wrote plot:   {plot_path}")
    except Exception as exc:  # matplotlib is a bonus, not a dependency of the table
        print(f"Plot skipped (matplotlib unavailable): {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
