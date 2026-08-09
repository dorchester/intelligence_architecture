"""Stage: the statistical class, run inside a build container.

    python stages/analyse_workforce.py <client_id>

Three techniques that between them cover most of what the workload's
analytical stages do, chosen because each stresses something different:

* **IPF raking** - iterative proportional fitting of survey weights to known
  population margins. Needs numpy and converges iteratively, so it is the
  test of whether real numeric work fits the build budget.
* **A logistic fit** - scikit-learn on the raked data, the shape every
  propensity or risk model takes.
* **Kish effective n** - the number that says how much the weighting cost in
  precision. Reporting a weighted estimate without it overstates confidence,
  which is the kind of error that survives review because nobody sees it.

Outputs aggregates JSON and a chart to the artifacts domain. No row-level
data leaves the container: IAM cannot inspect aggregation level, so that rule
is enforced here, in code, at the point of writing.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

from _aws import config, emit_metric, put_bytes, put_json, read_parquet

MAX_RAKE_ITERS = 50
TOLERANCE = 1e-6


def rake(df: pd.DataFrame, targets: dict[str, dict[str, float]]) -> np.ndarray:
    """Iterative proportional fitting.

    Starts every unit at weight 1 and repeatedly rescales so each dimension's
    weighted totals match its target margin. Adjusting one dimension disturbs
    the other, which is exactly why it iterates rather than solving once.
    """
    w = np.ones(len(df), dtype=float)

    for iteration in range(MAX_RAKE_ITERS):
        before = w.copy()
        for column, margin in targets.items():
            for level, target_share in margin.items():
                mask = (df[column] == level).to_numpy()
                current = w[mask].sum()
                if current <= 0:
                    continue
                # Scale this cell so its weighted share hits the target.
                w[mask] *= (target_share * w.sum()) / current
        shift = np.abs(w - before).max()
        if shift < TOLERANCE:
            print(f"rake converged after {iteration + 1} iterations (max shift {shift:.2e})")
            break
    else:
        print(f"rake hit the {MAX_RAKE_ITERS}-iteration cap without converging")

    # Normalise back to sample size so weights read as people, not shares.
    return w * (len(df) / w.sum())


def kish_effective_n(w: np.ndarray) -> float:
    """Effective sample size under weighting: (sum w)^2 / sum(w^2)."""
    return float((w.sum() ** 2) / (w ** 2).sum())


def fit_attrition_model(df: pd.DataFrame, w: np.ndarray) -> dict:
    """Weighted logistic fit of an attrition proxy on structural features."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline

    # Proxy: short tenure stands in for flight risk. A real stage would use
    # an observed outcome; the mechanics are identical.
    y = (df["tenure_years"] < 1.5).astype(int)
    if y.nunique() < 2:
        return {"skipped": "outcome has a single class"}

    categorical = [c for c in ("seniority_level", "department", "age_band") if c in df.columns]
    numeric = [c for c in ("skill_count",) if c in df.columns]

    pipe = Pipeline([
        ("prep", ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=10), categorical),
        ], remainder="passthrough")),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    X = df[categorical + numeric]
    pipe.fit(X, y, clf__sample_weight=w)

    return {
        "outcome": "tenure_years < 1.5",
        "n": int(len(df)),
        "positive_rate": round(float(y.mean()), 4),
        "weighted_positive_rate": round(float(np.average(y, weights=w)), 4),
        "accuracy_in_sample": round(float(pipe.score(X, y, sample_weight=w)), 4),
        "features": categorical + numeric,
    }


def chart(df: pd.DataFrame, w: np.ndarray, client_id: str) -> bytes:
    """Unweighted vs raked composition - the picture of what raking did."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    levels = sorted(df["seniority_level"].dropna().unique())
    raw = [float((df["seniority_level"] == lv).mean()) for lv in levels]
    wtd = [float(w[(df["seniority_level"] == lv).to_numpy()].sum() / w.sum()) for lv in levels]

    x = np.arange(len(levels))
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=140)
    ax.bar(x - 0.2, raw, 0.4, label="Unweighted sample", color="#6b7280")
    ax.bar(x + 0.2, wtd, 0.4, label="Raked to margins", color="#3b82f6")
    ax.set_xticks(x, levels, rotation=20, ha="right")
    ax.set_ylabel("Share of workforce")
    ax.set_title(f"Seniority composition before and after raking — {client_id}")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def main(client_id: str) -> None:
    started = time.time()
    cfg = config()
    lakehouse, artifacts = cfg["lakehouse_bucket"], cfg["artifacts_bucket"]

    df = read_parquet(lakehouse, f"silver/profiles/client_id={client_id}/part-0000.parquet")
    print(f"read {len(df):,} conformed rows through the silver-read grant")

    feedback = __import__("os").environ.get("REVISION_FEEDBACK", "").strip()
    if feedback:
        print(f"revision requested: {feedback}")

    # Known population margins this sample must be raked to. In the workload
    # these come from a census or a client-supplied headcount table.
    targets = {
        "seniority_level": {"Entry": 0.34, "Mid": 0.38, "Senior": 0.20,
                            "Director": 0.06, "VP/Executive": 0.02},
    }
    present = set(df["seniority_level"].dropna().unique())
    targets["seniority_level"] = {k: v for k, v in targets["seniority_level"].items()
                                  if k in present}
    total = sum(targets["seniority_level"].values())
    targets["seniority_level"] = {k: v / total for k, v in targets["seniority_level"].items()}

    w = rake(df, targets)
    neff = kish_effective_n(w)
    efficiency = neff / len(df)

    model = fit_attrition_model(df, w)

    aggregates = {
        "client_id": client_id,
        "n": int(len(df)),
        "effective_n": round(neff, 1),
        "design_efficiency": round(efficiency, 4),
        "weight_range": [round(float(w.min()), 3), round(float(w.max()), 3)],
        "mean_tenure_years_unweighted": round(float(df["tenure_years"].mean()), 3),
        "mean_tenure_years_raked": round(float(np.average(df["tenure_years"], weights=w)), 3),
        "seniority_shares_raked": {
            lv: round(float(w[(df["seniority_level"] == lv).to_numpy()].sum() / w.sum()), 4)
            for lv in sorted(present)
        },
        "attrition_model": model,
        "revision_feedback": feedback or None,
    }

    prefix = f"analysis/{client_id}"
    put_json(artifacts, f"{prefix}/aggregates.json", aggregates)
    put_bytes(artifacts, f"{prefix}/composition.png", chart(df, w, client_id), "image/png")

    elapsed = time.time() - started
    emit_metric("analyse_workforce", "DurationSeconds", elapsed, "Seconds")
    emit_metric("analyse_workforce", "EffectiveN", neff, "Count")

    print(f"effective n {neff:,.1f} of {len(df):,} "
          f"({efficiency:.1%} design efficiency)")
    print(f"mean tenure {aggregates['mean_tenure_years_unweighted']}y unweighted -> "
          f"{aggregates['mean_tenure_years_raked']}y raked")
    print(f"ANALYSIS OK in {elapsed:.1f}s | aggregates + chart -> s3://{artifacts}/{prefix}/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: analyse_workforce.py <client_id>")
    main(sys.argv[1])
