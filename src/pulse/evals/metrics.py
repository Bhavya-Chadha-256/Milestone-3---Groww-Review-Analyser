"""Classification metrics with an `unthemed` class and a seeded paired bootstrap (evaluation §4, §9.1)."""

import random
from collections import Counter
from collections.abc import Mapping, Sequence

from pulse.models import UNTHEMED, Taxonomy

PRF = tuple[float, float, float, int]  # precision, recall, f1, support


def class_labels(taxonomy: Taxonomy) -> list[str]:
    """Every taxonomy theme plus `unthemed`: the classes macro-F1 averages over (E-CLS-1)."""
    return [theme.id for theme in taxonomy.themes] + [UNTHEMED]


def macro_f1(
    gold: Mapping[str, str], pred: Mapping[str, str], classes: Sequence[str]
) -> tuple[float, dict[str, PRF]]:
    """Mean F1 over the classes that occur in gold or predictions; a missing prediction counts as wrong."""
    pairs = [(label, pred.get(key)) for key, label in gold.items()]
    per_class: dict[str, PRF] = {}
    for name in classes:
        true_pos = sum(truth == name and guess == name for truth, guess in pairs)
        false_pos = sum(truth != name and guess == name for truth, guess in pairs)
        false_neg = sum(truth == name and guess != name for truth, guess in pairs)
        if true_pos + false_pos + false_neg == 0:
            continue
        precision = true_pos / (true_pos + false_pos) if true_pos + false_pos else 0.0
        recall = true_pos / (true_pos + false_neg) if true_pos + false_neg else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[name] = (precision, recall, f1, true_pos + false_neg)
    score = sum(values[2] for values in per_class.values()) / len(per_class) if per_class else 0.0
    return score, per_class


def cohen_kappa(first: Mapping[str, str], second: Mapping[str, str]) -> float:
    keys = [key for key in first if key in second]
    if not keys:
        return 0.0
    observed = sum(first[key] == second[key] for key in keys) / len(keys)
    counts_a, counts_b = Counter(first[key] for key in keys), Counter(second[key] for key in keys)
    expected = sum(counts_a[label] * counts_b[label] for label in counts_a) / len(keys) ** 2
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def majority_baseline(gold: Mapping[str, str], classes: Sequence[str]) -> float:
    """Macro-F1 of always predicting the most frequent gold class (E-CLS-4)."""
    if not gold:
        return 0.0
    majority = Counter(gold.values()).most_common(1)[0][0]
    return macro_f1(gold, dict.fromkeys(gold, majority), classes)[0]


def paired_bootstrap_ci(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    gold: Mapping[str, str],
    pred_a: Mapping[str, str],
    pred_b: Mapping[str, str] | None = None,
    *,
    classes: Sequence[str],
    n: int = 1000,
    seed: int = 0,
) -> tuple[float, float]:
    """95% CI of macro-F1, or of macro-F1(b) − macro-F1(a) when `pred_b` is given (paired resampling)."""
    keys = sorted(gold)
    rng = random.Random(seed)
    scores = []
    for _ in range(n):
        drawn = [rng.choice(keys) for _ in keys]
        resampled_gold = {f"{index}:{key}": gold[key] for index, key in enumerate(drawn)}
        score_a = macro_f1(resampled_gold, _resample(pred_a, drawn), classes)[0]
        if pred_b is None:
            scores.append(score_a)
        else:
            scores.append(macro_f1(resampled_gold, _resample(pred_b, drawn), classes)[0] - score_a)
    scores.sort()
    return scores[int(0.025 * (n - 1))], scores[int(0.975 * (n - 1))]


def _resample(pred: Mapping[str, str], drawn: list[str]) -> dict[str, str]:
    return {f"{index}:{key}": pred[key] for index, key in enumerate(drawn) if key in pred}
