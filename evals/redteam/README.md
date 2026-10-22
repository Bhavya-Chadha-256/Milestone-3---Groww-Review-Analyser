# Red-team sets: all made up

`pii_reviews.csv` holds **synthetic** reviews seeded with **fake** personal details, written to test the PII scrubber (metric E-PII-3). The phone numbers, PAN, Aadhaar, UPI IDs, emails, IFSC codes and account numbers are invented and belong to no one.

- `seeded_pii` lists the fake values that must never survive scrubbing.
- `negative = true` marks rows that must stay unchanged, such as rupee amounts.
