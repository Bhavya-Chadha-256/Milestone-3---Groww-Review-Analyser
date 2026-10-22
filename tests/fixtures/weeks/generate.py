"""Seeded fixture weeks for the pulse evals (evaluation §3, Task 4.5).

Run: uv run python tests/fixtures/weeks/generate.py

Writes normal/, thin/ and burst/ under this directory, each with one `generic__data.csv` covering the
12 weeks 27 Jul – 18 Oct 2026 (window 2026-W42). Every English and Hinglish template has at least 8 words
and names exactly one theme's marker words, so a keyword classifier can label them in tests; the Hindi rows
exercise the language check and are excluded from themes. All text is synthetic.
"""

import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
FIRST_DAY = datetime(2026, 7, 27, 9, 0)
DAYS = 84  # 12 weeks
LATEST_WEEK_START = datetime(2026, 10, 12, 9, 0)
COLUMNS = ("id", "title", "text", "rating", "date", "version")

TEMPLATES: dict[str, list[str]] = {
    "withdrawals": [
        "My withdrawal request has been pending for {days} days without any update",
        "Withdrawal money not credited to my bank account even after {days} days",
        "Withdraw option shows processing forever and the amount is still blocked",
        "Paisa withdraw kiya tha lekin abhi tak bank mein nahi aaya",
        "Withdrawal fail ho gaya aur amount wapas wallet mein bhi nahi dikha",
        "Cannot withdraw my own funds, the button just keeps showing an error",
        "Instant withdrawal is not instant at all, it took {days} working days",
        "Withdrawal cancelled automatically and nobody tells me the reason for it",
        "Bhai withdraw karne mein itna time kyun lagta hai, bahut problem hai",
        "Every time I withdraw profits the money gets stuck in between somewhere",
    ],
    "kyc": [
        "KYC verification is pending for {days} days even after submitting every document",
        "My KYC got rejected twice without any clear reason given in the app",
        "KYC process keeps asking for the same documents again and again",
        "KYC abhi tak complete nahi hua, documents do baar upload kar diye",
        "Video KYC call never connects, it just keeps waiting on the screen",
        "Cannot start investing because KYC is stuck in review for weeks now",
        "KYC update for my new address has been under process for {days} days",
        "Mera KYC reject ho gaya aur reason bhi nahi bataya gaya kahin",
        "Signature upload in KYC fails every time with a vague error message",
        "Re-KYC request was submitted long ago but status still shows pending",
    ],
    "app_performance": [
        "The app crashes every time I open the portfolio page in the morning",
        "App is very slow during market hours and orders take forever to load",
        "Charts hang completely when the market opens, very frustrating to use",
        "App bahut slow chalta hai market open hote hi, kuch load nahi hota",
        "Screen freezes on the order page and I miss the right price",
        "After the latest update the app keeps crashing on my Android phone",
        "Loading spinner never stops on the holdings screen these days at all",
        "App hang ho jata hai jab bhi chart kholta hoon, bahut lag hai",
        "Too much lag while placing orders, the app freezes for several seconds",
        "Login screen keeps loading forever and the app crashes after that",
    ],
    "support": [
        "Customer care never replies to my emails, I have waited {days} days",
        "Support ticket closed without solving anything, very poor customer service",
        "No phone number to talk to a real person in customer support",
        "Customer care se baat karne ka koi option hi nahi hai app mein",
        "Support chat gives only automated replies that do not help at all",
        "Raised a support ticket {days} days ago and still no response from the team",
        "Support team keeps sending the same copy paste reply every time",
        "Customer support ne bola 48 hours mein solve hoga, ab tak kuch nahi",
        "Helpline only gives a bot, there is no way to reach a human agent",
        "Support executives do not read the issue properly before closing tickets",
    ],
    "charges": [
        "Brokerage charges are not shown clearly before I place an order",
        "Hidden charges were deducted from my account without any explanation given",
        "Too many fees on every trade, the brokerage is higher than shown",
        "Itne saare charges kyun kaat rahe ho har order pe, samajh nahi aata",
        "DP charges were taken even on a small sell order, very unfair",
        "The fee breakdown screen does not match what was actually deducted later",
        "Brokerage kam bola tha lekin charges bahut zyada kat rahe hain",
        "Annual maintenance charges were added without any notice to me",
        "Charges on mutual fund switch are confusing and not explained anywhere",
        "Every intraday trade has extra fees that are not mentioned upfront",
    ],
    "praise": [
        "Very good platform for beginners, simple and clean interface overall",
        "Best investing experience so far, everything is easy to understand here",
        "Bahut accha platform hai, sab kuch simple aur clear hai yahan",
        "I like how clean and simple the whole design looks on mobile",
        "Great place to start investing for students, I recommend it to friends",
    ],
}
HINDI = [
    "ऐप बहुत अच्छा है लेकिन कभी कभी धीमा चलता है",
    "पैसे निकालने में बहुत ज्यादा समय लगता है",
    "ग्राहक सेवा से कोई जवाब नहीं मिलता है",
    "शुल्क के बारे में कुछ भी साफ नहीं बताया गया",
]
MARKERS = {  # each English/Hinglish template names exactly one theme's markers (praise names none)
    "withdrawals": ("withdraw",),
    "kyc": ("kyc",),
    "app_performance": ("crash", "slow", "hang", "lag ", "freez", "loading"),
    "support": ("support", "customer care", "helpline"),
    "charges": ("charge", "brokerage", "fee"),
}
NORMAL_WEIGHTS = {
    "withdrawals": 0.22,
    "app_performance": 0.22,
    "support": 0.18,
    "charges": 0.14,
    "kyc": 0.12,
    "praise": 0.12,
}
THIN_WEIGHTS = {"withdrawals": 0.6, "charges": 0.4}
BURST_TEXT = "Withdrawal stuck again, money not received in my bank account yet"


def theme_of(text: str) -> str | None:
    """The theme whose marker words appear in `text`, or None (praise, Hindi)."""
    lowered = text.lower()
    return next((theme for theme, words in MARKERS.items() if any(word in lowered for word in words)), None)


def generate(out_root: Path) -> dict[str, int]:
    """Write the three datasets under `out_root`; returns rows written per dataset."""
    datasets = {
        "normal": _rows(random.Random(SEED), 300, NORMAL_WEIGHTS, hindi=20),
        "thin": _rows(random.Random(SEED), 120, THIN_WEIGHTS, hindi=0),
        "burst": _rows(random.Random(SEED), 300, NORMAL_WEIGHTS, hindi=20) + _burst(random.Random(SEED), 60),
    }
    for name, rows in datasets.items():
        _write(Path(out_root) / name / "generic__data.csv", rows)
    return {name: len(rows) for name, rows in datasets.items()}


def _rows(rng: random.Random, count: int, weights: dict[str, float], hindi: int) -> list[dict[str, str]]:
    themes = list(weights)
    rows = []
    for index in range(count):
        theme = rng.choices(themes, weights=[weights[name] for name in themes])[0]
        text = rng.choice(TEMPLATES[theme]).format(days=rng.randint(3, 15))
        rating = rng.choice([4, 5, 5]) if theme == "praise" else rng.choice([1, 1, 2, 2, 3])
        rows.append(_row(f"r{index:04d}", text, rating, _when(rng)))
    rows += [
        _row(f"h{index:04d}", rng.choice(HINDI), rng.choice([2, 3, 4]), _when(rng)) for index in range(hindi)
    ]
    return rows


def _burst(rng: random.Random, count: int) -> list[dict[str, str]]:
    return [
        _row(
            f"b{index:04d}", BURST_TEXT, 1, LATEST_WEEK_START + timedelta(minutes=rng.randint(0, 6 * 24 * 60))
        )
        for index in range(count)
    ]


def _when(rng: random.Random) -> datetime:
    return FIRST_DAY + timedelta(days=rng.randrange(DAYS), minutes=rng.randint(0, 12 * 60))


def _row(review_id: str, text: str, rating: int, when: datetime) -> dict[str, str]:
    return {
        "id": review_id,
        "title": "",
        "text": text,
        "rating": str(rating),
        "date": when.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "18.22.0",
    }


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    print(generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent))
