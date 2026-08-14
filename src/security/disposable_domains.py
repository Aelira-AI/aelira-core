"""
Disposable / throwaway email domain blocklist.

The educational-email validator only checks the *shape* of a domain
(`.edu`, `.edu.<cc>`, `.ac.<cc>`). Several country-code academic TLDs --
notably `.edu.pl`, `.edu.co`, `.edu.eu` -- are openly registrable, and
temp-mail services exploit them to pass the ".edu" gate and abuse the free
tier (see incident 2026-06-03, riley.diaz3@nullsto.edu.pl).

This module maintains an explicit blocklist of known disposable / temp-mail
domains so they can be rejected regardless of how "academic" the suffix looks.

Matching is suffix-based: a blocked entry `nullsto.edu.pl` also blocks
`mailbox.nullsto.edu.pl`, etc.

NOTE: This list needs occasional maintenance -- disposable providers add new
domains regularly. Treat it as a high-signal blocklist, not an exhaustive one;
the IP-velocity / rapid-signup heuristics in abuse_detector.py cover the rest.
"""

# Known disposable / temp-mail domains. Lowercase, no leading dot.
DISPOSABLE_DOMAINS = {
    # --- exploited the .edu / academic suffix loophole ---
    "nullsto.edu.pl",  # 2026-06-03 free-tier abuse
    # --- mainstream disposable / temp-mail providers ---
    "mailinator.com",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "sharklasers.com",
    "grr.la",
    "guerrillamailblock.com",
    "tempmail.com",
    "temp-mail.org",
    "tempmail.net",
    "tempmailo.com",
    "10minutemail.com",
    "10minutemail.net",
    "20minutemail.com",
    "throwawaymail.com",
    "throwaway.email",
    "getnada.com",
    "nada.email",
    "dispostable.com",
    "yopmail.com",
    "yopmail.net",
    "yopmail.fr",
    "trashmail.com",
    "trashmail.net",
    "maildrop.cc",
    "fakeinbox.com",
    "fakemail.net",
    "mohmal.com",
    "emailondeck.com",
    "moakt.com",
    "tempr.email",
    "discard.email",
    "spamgourmet.com",
    "mailnesia.com",
    "mintemail.com",
    "mytemp.email",
    "tmpmail.org",
    "tmpmail.net",
    "tmail.ws",
    "burnermail.io",
    "anonaddy.me",
    "33mail.com",
    "inboxkitten.com",
    "luxusmail.org",
    "linshiyou.com",
    "cs.email",
    "harakirimail.com",
    "minuteinbox.com",
    "easytrashmail.com",
    "wegwerfmail.de",
    "byom.de",
}


def is_disposable_domain(email_or_domain: str) -> bool:
    """
    Return True if the email address (or bare domain) belongs to a known
    disposable / temp-mail provider.

    Matching is suffix-based so subdomains of a blocked domain are also caught.
    """
    if not email_or_domain:
        return False

    domain = email_or_domain.strip().lower()
    if "@" in domain:
        domain = domain.split("@")[-1]
    domain = domain.rstrip(".")
    if not domain:
        return False

    # Exact match, or the domain is a subdomain of a blocked entry.
    for blocked in DISPOSABLE_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    return False
