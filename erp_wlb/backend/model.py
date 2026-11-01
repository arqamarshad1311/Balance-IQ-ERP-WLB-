"""
Machine learning model for WLB risk classification.

No dataset file is loaded from disk. Training rows are generated
programmatically (correlated synthetic samples, matching the scheme in the
project synopsis) and the model is trained once when the server starts.
Real submissions go to the database (see database.py), not to this
synthetic sample.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

FEATURES = [
    "work_hours", "commute", "sleep", "stress",
    "family_time", "social_time", "satisfaction", "mental_health",
]

# direction: +1 means higher value helps balance, -1 means higher value hurts it
DIRECTION = {
    "work_hours": -1, "commute": -1, "sleep": 1, "stress": -1,
    "family_time": 1, "social_time": 1, "satisfaction": 1, "mental_health": 1,
}

FEATURE_RANGES = {
    "work_hours": (20, 80), "commute": (0, 4), "sleep": (3, 10), "stress": (1, 10),
    "family_time": (0, 6), "social_time": (0, 20), "satisfaction": (1, 10), "mental_health": (1, 10),
}


def _generate_synthetic_rows(n=2000, seed=42):
    """Correlated synthetic sample: high work hours -> more stress, less sleep."""
    rng = np.random.default_rng(seed)
    work_hours = rng.normal(45, 12, n).clip(20, 80)
    stress = (0.12 * work_hours + rng.normal(3, 1.8, n)).clip(1, 10)
    sleep = (9.5 - 0.05 * work_hours - 0.15 * stress + rng.normal(0, 0.8, n)).clip(3, 10)
    commute = rng.normal(1.1, 0.6, n).clip(0, 4)
    family_time = (3.2 - 0.02 * work_hours + rng.normal(0, 0.8, n)).clip(0, 6)
    social_time = (8 - 0.05 * work_hours + rng.normal(0, 3, n)).clip(0, 20)
    satisfaction = (8 - 0.35 * stress + rng.normal(0, 1.5, n)).clip(1, 10)
    mental_health = (8 - 0.4 * stress + 0.3 * sleep / 2 + rng.normal(0, 1.3, n)).clip(1, 10)

    X = np.column_stack([work_hours, commute, sleep, stress, family_time, social_time, satisfaction, mental_health])

    # ground-truth WLB score used only to LABEL the synthetic rows
    score = (
        0.35 * (sleep - 6.5) + 0.30 * (family_time - 2) + 0.15 * (social_time - 6)
        + 0.25 * (satisfaction - 5.5) + 0.30 * (mental_health - 5.5)
        - 0.10 * (work_hours - 45) - 0.20 * (commute - 1) - 0.35 * (stress - 5)
    )
    labels = np.where(score > 2.5, "Low Risk", np.where(score > -1.0, "Medium Risk", "High Risk"))
    return X, labels


class WLBModel:
    def __init__(self):
        X, y = _generate_synthetic_rows()
        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        self.clf = LogisticRegression(max_iter=500)
        self.clf.fit(Xs, y)
        self.classes_ = list(self.clf.classes_)

    def predict(self, features: dict):
        """features: dict of the 8 FEATURES -> value. Returns label, score(0..1), contributions."""
        x = np.array([[features[f] for f in FEATURES]])
        xs = self.scaler.transform(x)
        proba = self.clf.predict_proba(xs)[0]
        label = str(self.classes_[int(np.argmax(proba))])

        order = {"High Risk": 0, "Medium Risk": 1, "Low Risk": 2}
        risk_score = sum(p * order[c] for c, p in zip(self.classes_, proba)) / 2  # 0..1

        # per-feature contribution: standardized deviation * coefficient for predicted class
        class_idx = self.classes_.index(label)
        coefs = self.clf.coef_[class_idx] if len(self.classes_) > 2 else self.clf.coef_[0]
        contributions = {}
        for i, f in enumerate(FEATURES):
            contributions[f] = float(xs[0][i] * coefs[i])

        top_factor = max(contributions, key=lambda k: abs(contributions[k]))
        return {
            "label": label,
            "score": round(float(risk_score), 3),
            "proba": {str(c): round(float(p), 3) for c, p in zip(self.classes_, proba)},
            "contributions": {k: round(v, 3) for k, v in contributions.items()},
            "top_factor": top_factor,
        }


# trained once at import time (module-level singleton, used by main.py)
model = WLBModel()


# ---------------------------------------------------------------------------
# ERP additions: labels + rule-based suggestions shown next to every prediction
# ---------------------------------------------------------------------------
FEATURE_LABELS = {
    "work_hours": "Work hours", "commute": "Commute time", "sleep": "Sleep",
    "stress": "Stress level", "family_time": "Family / personal time",
    "social_time": "Social time", "satisfaction": "Job satisfaction",
    "mental_health": "Mental health",
}


def suggestions(f: dict) -> list:
    """Simple rule engine -> up to 4 practical suggestions."""
    tips = []
    if f["work_hours"] > 50:
        tips.append("Work hours are high - set a target to bring the week closer to 45 hours.")
    if f["sleep"] < 6.5:
        tips.append("Sleep is on the low side - even 30 extra minutes a night helps recovery.")
    if f["stress"] >= 7:
        tips.append("Stress is high - consider a short stress-management routine or a check-in with your manager.")
    if f["commute"] > 1.5:
        tips.append("Long commute - ask about flexible or hybrid days to cut commute load.")
    if f["family_time"] < 1.5:
        tips.append("Block a fixed slot each evening for family or personal time.")
    if f["satisfaction"] <= 4:
        tips.append("Job satisfaction is low - worth a conversation about role fit or workload.")
    if f["mental_health"] <= 4:
        tips.append("Mental health score is low - the organisation's wellbeing / EAP resources may help.")
    if not tips:
        tips.append("You're in a good place - keep protecting the habits that are working.")
    return tips[:4]
