"""PERMIT preference predictor fitted to the Wu et al. permission data.

    uv run python -m src.permit.predictor            # fit, calibrate, report

Data: Wu, Yang, Roesner, Kohno, Zhang, Iqbal, "Towards Automating Data Access
Permissions in AI Agents" (IEEE S&P 2026), data/user_study.json, CC BY 4.0.
Read in place from the ai-agent-permissions checkout (override with the
PERMIT_WU_DATA environment variable).

Label: would the user say yes if asked now? "Yes, always share" and "Yes, but
ask me next time" -> 1; "No, but ask me next time" and "No, never share" -> 0.

Design (all splits are participant-level, never instance-level):
- participants are split train / calibration / test (default 60/20/20);
- a held-out participant's answers are split *by scenario* into a profile
  (what the predictor may see) and targets, so a scenario never straddles both;
- model: logistic regression on four features -- intercept, logit of the
  item's grant rate among training participants, logit of the participant's
  own grant rate on their profile, and logit of a user-kNN estimate from
  training participants who agree with the profile;
- lower bound: predictions are binned on the calibration participants and each
  bin gets a one-sided Clopper-Pearson lower bound on its grant rate. This is
  a bound on the *bin's average* grant rate for exchangeable participants, not
  a per-person guarantee; coverage is checked on the test participants.

Predicted labels are never turned into grants: the estimate only steers asking.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = PROJECT_ROOT.parent / "ai-agent-permissions" / "data" / "user_study.json"
YES_LABELS = {"Yes, always share", "Yes, but ask me next time"}
ALL_LABELS = YES_LABELS | {"No, but ask me next time", "No, never share"}
EPS = 0.02


# ---- data ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Answer:
    user: str
    query: int
    item: str      # "<app>,<data type>"
    label: str
    y: int


def data_path() -> Path:
    return Path(os.environ.get("PERMIT_WU_DATA", DEFAULT_DATA))


def load_answers(path: Optional[Path] = None, valid_only: bool = True) -> List[Answer]:
    with open(path or data_path(), "r", encoding="utf-8") as handle:
        participants = json.load(handle)
    out: List[Answer] = []
    for p in participants:
        if valid_only and p.get("Valid") is False:
            continue
        for task in p.get("task3", []):
            for item, label in (task.get("answer") or {}).items():
                if label in ALL_LABELS:
                    out.append(Answer(p["uuid"], int(task["id"]), item, label, int(label in YES_LABELS)))
    return out


def by_user(answers: Iterable[Answer]) -> Dict[str, List[Answer]]:
    users: Dict[str, List[Answer]] = defaultdict(list)
    for a in answers:
        users[a.user].append(a)
    return dict(users)


def split_participants(users: Sequence[str], seed: int = 0,
                       fractions: Tuple[float, float, float] = (0.6, 0.2, 0.2)) -> Dict[str, List[str]]:
    ids = sorted(users)
    random.Random(seed).shuffle(ids)
    n_train = int(round(fractions[0] * len(ids)))
    n_cal = int(round(fractions[1] * len(ids)))
    return {"train": ids[:n_train], "cal": ids[n_train:n_train + n_cal], "test": ids[n_train + n_cal:]}


def profile_target_split(answers: List[Answer], seed: int, frac: float = 0.5) -> Tuple[List[Answer], List[Answer]]:
    """Split one participant's answers by scenario (query id)."""
    queries = sorted({a.query for a in answers})
    rng = random.Random(f"{seed}:{answers[0].user}")
    rng.shuffle(queries)
    cut = max(1, int(round(frac * len(queries))))
    prof_q = set(queries[:cut])
    return [a for a in answers if a.query in prof_q], [a for a in answers if a.query not in prof_q]


# ---- model ------------------------------------------------------------------------------
def _logit(p: float) -> float:
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


class Predictor:
    """Logistic model over item rate, user rate and user-kNN features."""

    FEATURES = ("intercept", "logit_item_rate", "logit_user_rate", "logit_knn_rate")

    def __init__(self, item_smoothing: float = 2.0, user_smoothing: float = 4.0,
                 knn_smoothing: float = 2.0, l2: float = 1e-3):
        self.a, self.m, self.k, self.l2 = item_smoothing, user_smoothing, knn_smoothing, l2
        self.weights = np.zeros(len(self.FEATURES))

    # pool statistics from training participants
    def _build_pool(self, train: Dict[str, List[Answer]]) -> None:
        self.pool: Dict[str, Dict[str, int]] = {}
        self.item_sum: Dict[str, float] = defaultdict(float)
        self.item_n: Dict[str, int] = defaultdict(int)
        self.item_users: Dict[str, List[str]] = defaultdict(list)
        for user, answers in train.items():
            prefs = {}
            for a in answers:
                prefs[a.item] = a.y   # last answer wins if an item repeats
            self.pool[user] = prefs
            for item, y in prefs.items():
                self.item_sum[item] += y
                self.item_n[item] += 1
                self.item_users[item].append(user)
        total = sum(self.item_n.values())
        self.global_rate = sum(self.item_sum.values()) / total if total else 0.5

    def item_rate(self, item: str, exclude: Optional[str] = None) -> float:
        s, n = self.item_sum.get(item, 0.0), self.item_n.get(item, 0)
        if exclude is not None and item in self.pool.get(exclude, {}):
            s -= self.pool[exclude][item]
            n -= 1
        return (s + self.a * self.global_rate) / (n + self.a)

    def features(self, profile: Dict[str, int], item: str, exclude: Optional[str] = None) -> np.ndarray:
        ir = self.item_rate(item, exclude)
        ur = (sum(profile.values()) + self.m * self.global_rate) / (len(profile) + self.m)
        num = den = 0.0
        for v in self.item_users.get(item, []):
            if v == exclude:
                continue
            prefs = self.pool[v]
            common = [i for i in profile if i in prefs and i != item]
            if not common:
                continue
            agree = sum(profile[i] == prefs[i] for i in common)
            w = max(0.0, (2 * agree - len(common)) / (len(common) + 3))
            num += w * prefs[item]
            den += w
        knn = (num + self.k * ir) / (den + self.k)
        return np.array([1.0, _logit(ir), _logit(ur), _logit(knn)])

    def fit(self, train: Dict[str, List[Answer]], seed: int = 0, repeats: int = 2) -> "Predictor":
        self._build_pool(train)
        rows, ys = [], []
        for r in range(repeats):
            for user, answers in train.items():
                if len({a.query for a in answers}) < 2:
                    continue
                prof, targ = profile_target_split(answers, seed + r)
                profile = {a.item: a.y for a in prof}
                for a in targ:
                    if a.item in profile:
                        continue
                    rows.append(self.features(profile, a.item, exclude=user))
                    ys.append(a.y)
        X, y = np.array(rows), np.array(ys, dtype=float)
        w = np.zeros(X.shape[1])
        for _ in range(50):  # IRLS / Newton with a small ridge
            p = _sigmoid(X @ w)
            grad = X.T @ (p - y) + self.l2 * w
            H = (X * (p * (1 - p))[:, None]).T @ X + self.l2 * np.eye(len(w))
            step = np.linalg.solve(H, grad)
            w -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        self.weights = w
        self.n_train_rows = len(ys)
        return self

    def predict(self, profile: Dict[str, int], item: str) -> float:
        return float(_sigmoid(self.features(profile, item) @ self.weights))

    def predict_item_prior(self, item: str) -> float:
        return self.item_rate(item)


# ---- lower bound ---------------------------------------------------------------------------
def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p) for i in range(k, n + 1)]
    top = max(logs)
    return min(1.0, math.exp(top) * sum(math.exp(x - top) for x in logs))


def clopper_pearson_lower(k: int, n: int, alpha: float) -> float:
    """One-sided (1 - alpha) lower confidence bound on a binomial rate."""
    if n == 0 or k == 0:
        return 0.0
    lo, hi = 0.0, k / n
    for _ in range(60):
        mid = (lo + hi) / 2
        if _binom_sf(k, n, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return lo


class BinnedLowerBound:
    """Histogram-binned calibration with a lower bound on each bin's grant rate.

    method="cluster" (default): participant-cluster bootstrap. Calibration
    participants are resampled with replacement and the bound is the alpha
    quantile of the bin's pooled grant rate. Answers from one person are
    correlated, so resampling people (not answers) is what makes the bound
    hold for *new participants*.
    method="predictive": lower bound for the grant rate of a *new group* of
    participants of the same size as the calibration group: bin rate minus
    z(1 - alpha) * bootstrap s.e. * sqrt(2), accounting for both the
    calibration group's and the new group's sampling noise. This is the one a
    held-out test group can check, so it is the default for EVA.
    method="cp": Clopper-Pearson, treating answers as independent. Kept for
    comparison; it is too narrow on this data.
    """

    def __init__(self, n_bins: int = 8, alpha: float = 0.1, method: str = "predictive",
                 n_boot: int = 2000, seed: int = 0):
        if method not in ("cluster", "predictive", "cp"):
            raise ValueError("method must be 'cluster', 'predictive' or 'cp'")
        self.n_bins, self.alpha, self.method = n_bins, alpha, method
        self.n_boot, self.seed = n_boot, seed

    def fit(self, p: Sequence[float], y: Sequence[int],
            groups: Optional[Sequence[str]] = None) -> "BinnedLowerBound":
        p, y = np.asarray(p, float), np.asarray(y, int)
        if self.method != "cp" and groups is None:
            raise ValueError("the cluster bound needs participant ids (groups)")
        qs = np.quantile(p, np.linspace(0, 1, self.n_bins + 1))
        self.edges = np.unique(qs)
        self.edges[0], self.edges[-1] = 0.0, 1.0 + 1e-9
        idx = self._bin(p)
        n_bins = len(self.edges) - 1
        if self.method != "cp":
            people = sorted(set(groups))
            pos = {g: i for i, g in enumerate(people)}
            g_idx = np.array([pos[g] for g in groups])
            k_mat = np.zeros((len(people), n_bins))
            n_mat = np.zeros((len(people), n_bins))
            np.add.at(k_mat, (g_idx, idx), y)
            np.add.at(n_mat, (g_idx, idx), 1)
            rng = np.random.default_rng(self.seed)
            draws = rng.integers(0, len(people), size=(self.n_boot, len(people)))
            k_boot = k_mat[draws].sum(axis=1)
            n_boot = n_mat[draws].sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                rates = np.where(n_boot > 0, k_boot / n_boot, 0.0)
            if self.method == "cluster":
                lowers = np.quantile(rates, self.alpha, axis=0)
            else:
                z = NormalDist().inv_cdf(1 - self.alpha)
                k_all, n_all = k_mat.sum(axis=0), n_mat.sum(axis=0)
                point = np.where(n_all > 0, k_all / np.maximum(n_all, 1), 0.0)
                lowers = np.clip(point - z * rates.std(axis=0) * math.sqrt(2), 0.0, 1.0)
        self.bins = []
        for b in range(n_bins):
            mask = idx == b
            k, n = int(y[mask].sum()), int(mask.sum())
            lower = float(lowers[b]) if self.method != "cp" else clopper_pearson_lower(k, n, self.alpha)
            self.bins.append({"lo": float(self.edges[b]), "hi": float(min(1.0, self.edges[b + 1])),
                              "n": n, "rate": k / n if n else float("nan"), "lower": lower})
        return self

    def _bin(self, p: np.ndarray) -> np.ndarray:
        return np.clip(np.searchsorted(self.edges, p, side="right") - 1, 0, len(self.edges) - 2)

    def lower(self, p: float) -> float:
        return self.bins[int(self._bin(np.array([p]))[0])]["lower"]

    def coverage(self, p: Sequence[float], y: Sequence[int]) -> List[Dict[str, float]]:
        p, y = np.asarray(p, float), np.asarray(y, int)
        idx = self._bin(p)
        rows = []
        for b, info in enumerate(self.bins):
            mask = idx == b
            n = int(mask.sum())
            rate = float(y[mask].mean()) if n else float("nan")
            rows.append({**info, "test_n": n, "test_rate": rate,
                         "covered": bool(n and rate >= info["lower"])})
        return rows


# ---- evaluation ---------------------------------------------------------------------------------
def predictions_for(users: Dict[str, List[Answer]], model: Predictor, seed: int,
                    prior_only: bool = False) -> Tuple[List[float], List[int], List[Answer]]:
    ps, ys, rows = [], [], []
    for user, answers in sorted(users.items()):
        if len({a.query for a in answers}) < 2:
            continue
        prof, targ = profile_target_split(answers, seed)
        profile = {a.item: a.y for a in prof}
        for a in targ:
            if a.item in profile:
                continue
            ps.append(model.predict_item_prior(a.item) if prior_only else model.predict(profile, a.item))
            ys.append(a.y)
            rows.append(a)
    return ps, ys, rows


def auc(p: Sequence[float], y: Sequence[int]) -> float:
    p, y = np.asarray(p, float), np.asarray(y, int)
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    for v in np.unique(p):
        m = p == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def metrics(p: Sequence[float], y: Sequence[int], labels: Optional[Sequence[str]] = None) -> Dict[str, float]:
    p, y = np.asarray(p, float), np.asarray(y, int)
    out = {"n": int(len(y)), "base_rate": float(y.mean()),
           "accuracy": float(((p >= 0.5) == y).mean()), "auc": auc(p, y),
           "brier": float(((p - y) ** 2).mean())}
    edges = np.linspace(0, 1, 11)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, 9)
    out["ece"] = float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean()
                           for b in range(10) if (idx == b).any()))
    if labels is not None:
        strict = np.array([l in ("Yes, always share", "No, never share") for l in labels])
        if strict.any():
            out["accuracy_always_never"] = float(((p[strict] >= 0.5) == y[strict]).mean())
            out["n_always_never"] = int(strict.sum())
    return out


@dataclass
class TrainedPredictor:
    model: Predictor
    bound: BinnedLowerBound
    split: Dict[str, List[str]]
    users: Dict[str, List[Answer]]
    seed: int

    def estimate(self, profile: Dict[str, int], item: str) -> Tuple[float, float]:
        p = self.model.predict(profile, item)
        return p, self.bound.lower(p)


# Default alpha: the predictive bound under-covers at its nominal level because
# groups of participants differ more than the bootstrap captures (coverage
# study, 10 splits, 2026-11-14: nominal 90% -> 78% empirical, 95% -> 85%,
# 97.5% -> 89%, 99% -> 90%). alpha = 0.01 is the smallest level reaching ~90%
# empirical coverage; it was chosen from that study, so re-check it on new data.
DEFAULT_ALPHA = 0.01


def train(seed: int = 0, alpha: float = DEFAULT_ALPHA, n_bins: int = 8,
          path: Optional[Path] = None, method: str = "predictive") -> TrainedPredictor:
    users = by_user(load_answers(path))
    split = split_participants(list(users), seed)
    model = Predictor().fit({u: users[u] for u in split["train"]}, seed=seed)
    p_cal, y_cal, rows = predictions_for({u: users[u] for u in split["cal"]}, model, seed)
    bound = BinnedLowerBound(n_bins, alpha, method=method, seed=seed).fit(
        p_cal, y_cal, groups=[r.user for r in rows])
    return TrainedPredictor(model, bound, split, users, seed)


def report(tp: TrainedPredictor) -> str:
    test = {u: tp.users[u] for u in tp.split["test"]}
    p, y, rows = predictions_for(test, tp.model, tp.seed)
    p0, _, _ = predictions_for(test, tp.model, tp.seed, prior_only=True)
    labels = [r.label for r in rows]
    m, m0 = metrics(p, y, labels), metrics(p0, y, labels)
    lines = [f"participants: train {len(tp.split['train'])}, calibration {len(tp.split['cal'])}, "
             f"test {len(tp.split['test'])} (seed {tp.seed}); training rows {tp.model.n_train_rows}",
             "weights: " + ", ".join(f"{n}={w:+.3f}" for n, w in zip(Predictor.FEATURES, tp.model.weights)),
             "", f"{'test metric':<26}{'predictor':>11}{'item prior':>12}"]
    for key in ("n", "base_rate", "accuracy", "auc", "brier", "ece", "accuracy_always_never", "n_always_never"):
        a, b = m.get(key), m0.get(key)
        fmt = (lambda v: f"{v:>11d}") if isinstance(a, int) else (lambda v: f"{v:>11.3f}")
        lines.append(f"{key:<26}{fmt(a)}{fmt(b) if b is not None else '':>12}")
    lines += ["", f"lower bound ({tp.bound.method}, alpha {tp.bound.alpha}): calibration bins vs held-out test participants",
              f"{'p range':<16}{'cal n':>7}{'cal rate':>10}{'lower':>8}{'test n':>8}{'test rate':>11}{'covered':>9}"]
    cov = tp.bound.coverage(p, y)
    for r in cov:
        lines.append(f"{r['lo']:.2f}-{r['hi']:.2f}{'':<6}{r['n']:>7}{r['rate']:>10.3f}{r['lower']:>8.3f}"
                     f"{r['test_n']:>8}{r['test_rate']:>11.3f}{'yes' if r['covered'] else 'NO':>9}")
    covered = sum(r["covered"] for r in cov)
    lines.append(f"bins covered: {covered}/{len(cov)} (target: about {1 - tp.bound.alpha:.0%} or more)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit and report the PERMIT preference predictor")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--bins", type=int, default=8)
    parser.add_argument("--method", choices=["predictive", "cluster", "cp"], default="predictive")
    parser.add_argument("--coverage-seeds", type=int, default=0,
                        help="Also report bin coverage over this many random participant splits.")
    args = parser.parse_args()
    print(report(train(args.seed, args.alpha, args.bins, method=args.method)))
    if args.coverage_seeds:
        print()
        print(coverage_study(args.coverage_seeds, args.alpha, args.bins))
    return 0


def coverage_study(n_seeds: int, alpha: float = DEFAULT_ALPHA, n_bins: int = 8) -> str:
    """Fraction of (split, bin) cells where the held-out test rate is >= the bound."""
    lines = [f"coverage over {n_seeds} participant splits (alpha {alpha}): share of bins with "
             "test rate >= lower bound, and mean slack (cal rate - lower)"]
    for method in ("cp", "cluster", "predictive"):
        covered = total = 0
        slack = []
        accs = []
        for seed in range(n_seeds):
            tp = train(seed, alpha, n_bins, method=method)
            p, y, _ = predictions_for({u: tp.users[u] for u in tp.split["test"]}, tp.model, seed)
            accs.append(metrics(p, y)["accuracy"])
            for r in tp.bound.coverage(p, y):
                if r["test_n"]:
                    total += 1
                    covered += r["covered"]
                    slack.append(r["rate"] - r["lower"])
        lines.append(f"  {method:<8} covered {covered}/{total} = {covered / total:.0%}, "
                     f"mean slack {np.mean(slack):.3f}, test accuracy {np.mean(accs):.3f} +/- {np.std(accs):.3f}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
