"""Ready-made universal fact-extraction patterns for SimpleSearchProfile.

These are domain-agnostic. Domain-specific facts (tax IDs, SKUs, MOQ…) should
be defined by the consumer via ``hint_patterns`` with their own regexes.

Usage:

    from nexusearch.hints import EMAIL_PATTERN, PHONE_PATTERN

    SimpleSearchProfile(
        ...,
        hint_patterns={"email": EMAIL_PATTERN, "phone": PHONE_PATTERN},
    )
"""

# Loose but practical email matcher (no capture group → whole match is used).
EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

# Loose international phone matcher: +7 (999) 123-45-67, +1 555-123-4567, etc.
# Requires at least 8 digits-ish characters to avoid matching random numbers.
PHONE_PATTERN = r"\+\d[\d\s().-]{7,}\d"
