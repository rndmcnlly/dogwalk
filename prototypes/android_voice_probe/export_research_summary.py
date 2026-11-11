"""Export only named synthetic experiment summaries/examples for the reference PR.

No globbing of call artifacts, credentials, private media, or provider payloads.
Run from this folder after the named local experiments exist.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
RUNS = (
    "frontend-bakeoff-016db479", "frontend-bakeoff-7243977c",
    "frontend-diverse-9ef26e53", "frontend-requests-b915df2f",
    "frontend-requests-b796f2d6",
)


def main():
    output = {"scope": "Selected synthetic-only development evidence, not independent acceptance or phone benchmarks.",
              "private_material_excluded": True, "runs": [], "examples": []}
    for name in RUNS:
        data = json.loads((ROOT / ".tools" / name / "summary.json").read_text())
        # Summary rows are aggregate scalar metrics. Never export warmups,
        # messages, runtime configuration or the absolute local output path.
        rows = data["summary"]
        assert all(all(v is None or isinstance(v, (str, int, float, bool, dict)) for v in row.values()) for row in rows)
        output["runs"].append({"run": name, "requests": data.get("requests"), "summary": rows})
    name = "frontend-requests-b796f2d6"
    selected = {"two_distinct_questions_regression", "same_session_distinct_topics", "permission_plus_invitation"}
    for line in (ROOT / ".tools" / name / "results.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["case"] not in selected:
            continue
        output["examples"].append({"model": row["model"], "design": row["design"], "case": row["case"],
                                   "supersession": row["supersession"],
                                   "incorrect_supersession": row["unsafe_supersessions"],
                                   "spoken_synthetic_text": [r.get("heard", "") for r in row["receipts"]]})
    target = ROOT / "research_results.json"
    target.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Exported {len(output['runs'])} named synthetic summaries and {len(output['examples'])} examples.")


if __name__ == "__main__":
    main()
