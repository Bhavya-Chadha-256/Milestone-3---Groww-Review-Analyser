import pytest

from pulse.privacy.scrub import PLACEHOLDER_RE, PiiMatch, Scrubber, find_pii


@pytest.mark.parametrize(
    "case",
    [
        ("mail rahul.s@gmail.com now", "mail [email] now"),
        ("mail rahul [at] gmail [dot] com now", "mail [email] now"),  # PII-6
        ("call +91 98765 43210", "call [phone]"),
        ("call 9876543210", "call [phone]"),
        ("PAN ABCDE1234F pls", "PAN [pan] pls"),
        ("PAN ABCDE 1234 F pls", "PAN [pan] pls"),  # PII-6
        ("aadhaar 2345 6789 0123", "aadhaar [aadhaar]"),
        ("pay rahul@okaxis", "pay [upi]"),
        ("ifsc HDFC0001234", "ifsc [ifsc]"),
        ("acct 123456789012345", "acct [account]"),
        ("see https://groww.in/x?y=1", "see [url]"),
    ],
)
def test_scrub_redacts_each_kind(case: tuple[str, str]) -> None:  # PII-1
    text, expected = case
    assert Scrubber().scrub(text).text == expected


@pytest.mark.parametrize(
    "text",
    [
        "lost ₹1,00,000 in F&O",
        "Rs 50000 stuck since Monday",
        "₹ 250000000 withdrawal pending",
        "INR 1234567890 debited",
        "I was at home, dot the i's",
    ],
)
def test_currency_amounts_are_not_redacted(text: str) -> None:  # PII-2
    assert Scrubber().scrub(text).text == text


def test_extra_patterns_redact_as_id() -> None:  # PII-7
    assert Scrubber([r"GRW\d{8}"]).scrub("order GRW12345678 stuck").text == "order [id] stuck"


def test_counts_and_find_pii() -> None:
    text = "mail a.b@x.com or c.d@y.org, call 9876543210"
    result = Scrubber().scrub(text)
    assert result.text == "mail [email] or [email], call [phone]"
    assert result.counts == {"email": 2, "phone": 1}
    matches = find_pii(text)
    assert matches == [PiiMatch("email", 5, 14), PiiMatch("email", 18, 27), PiiMatch("phone", 34, 44)]
    assert [text[match.start : match.end] for match in matches] == ["a.b@x.com", "c.d@y.org", "9876543210"]


def test_clean_text_is_unchanged_with_no_counts() -> None:
    result = Scrubber().scrub("Charts are slow during market open")
    assert result.text == "Charts are slow during market open" and result.counts == {}


def test_placeholder_regex() -> None:  # PII-9
    found = PLACEHOLDER_RE.findall("[email] [phone] [pan] [aadhaar] [upi] [ifsc] [account] [url] [id] [foo]")
    assert len(found) == 9
    assert PLACEHOLDER_RE.search("plain [brackets] text") is None


@pytest.mark.parametrize(
    "case",
    [
        ("aadhaar numbers 2345 6789 0123", "aadhaar numbers [aadhaar]"),
        ("support never answers 9876543210", "support never answers [phone]"),
        ("for 3 years 9876543210 is my number", "for 3 years [phone] is my number"),
        ("after 2 hrs 9876543210", "after 2 hrs [phone]"),
        ("Mrs 9876543210", "Mrs [phone]"),
        ("account no. in my orders 123456789012", "account no. in my orders [account]"),
    ],
)
def test_currency_guard_needs_a_currency_word(case: tuple[str, str]) -> None:  # PII-1, PII-2
    text, expected = case
    assert Scrubber().scrub(text).text == expected


@pytest.mark.parametrize(
    "case",
    [
        ("Pay me at rahul@ybl.", "Pay me at [upi]."),
        ("send to rahul.k@okaxis. thanks", "send to [upi]. thanks"),
        ("pan: abcde1234f", "pan: [pan]"),
        ("PAN no. AbCdE1234f", "PAN no. [pan]"),
        ("call 987-654-3210", "call [phone]"),
        ("call 98 76 54 32 10 now", "call [phone] now"),
        ("call 9876 543 210", "call [phone]"),
    ],
)
def test_common_pii_spellings_are_redacted(case: tuple[str, str]) -> None:  # PII-1
    text, expected = case
    assert Scrubber().scrub(text).text == expected


def test_spaced_lowercase_words_are_not_taken_for_pan() -> None:
    assert (
        Scrubber().scrub("since 2023 I have used this app daily").text
        == "since 2023 I have used this app daily"
    )
