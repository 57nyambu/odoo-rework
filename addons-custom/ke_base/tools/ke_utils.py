"""Pure-Python helpers for Kenya-specific data formats.

Nothing in here touches the ORM, so the functions can be imported and unit
tested anywhere (models, controllers, scripts) without an environment.
"""
import re

# Kenyan mobile subscriber numbers are 9 digits starting with 7 or 1
# (Safaricom, Airtel, Telkom, Equitel...). They are written locally as
# 07XX XXX XXX / 01XX XXX XXX and internationally as +254 7XX XXX XXX.
_KE_MOBILE_RE = re.compile(r"^(?:254|0)?([71]\d{8})$")

# KRA PIN: 11 characters. "A" + 9 digits + letter for individuals, "P" + 9
# digits + letter for non-individuals (companies, partnerships, trusts...).
_KRA_PIN_RE = re.compile(r"^[AP]\d{9}[A-Z]$")

# M-Pesa transaction (receipt) codes are 10 upper-case alphanumerics,
# e.g. "SJK3ABCD9Z". Older codes were also 10 characters.
_MPESA_CODE_RE = re.compile(r"^[A-Z0-9]{10}$")


def normalize_ke_msisdn(phone):
    """Normalize a Kenyan mobile number to the ``2547XXXXXXXX`` /
    ``2541XXXXXXXX`` MSISDN format expected by Safaricom Daraja.

    Accepts ``0712345678``, ``712345678``, ``254712345678``,
    ``+254 712 345 678``, ``(+254) 712-345-678``...

    :return: the normalized MSISDN, or ``None`` when the input is not a
        Kenyan mobile number.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    match = _KE_MOBILE_RE.match(digits)
    if not match:
        return None
    return f"254{match.group(1)}"


def format_ke_phone(phone):
    """Human-friendly local format: ``0712 345 678``. Returns the input
    unchanged when it is not a Kenyan mobile number."""
    msisdn = normalize_ke_msisdn(phone)
    if not msisdn:
        return phone
    local = "0" + msisdn[3:]
    return f"{local[:4]} {local[4:7]} {local[7:]}"


def mask_ke_phone(phone):
    """Mask a phone number for printing on receipts and logs, as required
    by the Kenya Data Protection Act, 2019 (data minimisation):
    ``254712345678`` -> ``0712***678``."""
    msisdn = normalize_ke_msisdn(phone)
    if not msisdn:
        return phone and "***"
    local = "0" + msisdn[3:]
    return f"{local[:4]}***{local[-3:]}"


def normalize_kra_pin(pin):
    """Upper-case a KRA PIN and strip whitespace/separators."""
    if not pin:
        return pin
    return re.sub(r"[\s\-./]", "", str(pin)).upper()


def is_valid_kra_pin(pin):
    """Return True when ``pin`` has the KRA PIN shape (A/P + 9 digits +
    letter). This is a format check only: KRA's iTax PIN checker remains
    the source of truth for whether a PIN is registered."""
    return bool(pin) and bool(_KRA_PIN_RE.match(normalize_kra_pin(pin)))


def kra_pin_taxpayer_type(pin):
    """``'individual'`` for A-PINs, ``'non_individual'`` for P-PINs."""
    pin = normalize_kra_pin(pin)
    if not is_valid_kra_pin(pin):
        return None
    return "individual" if pin.startswith("A") else "non_individual"


def normalize_mpesa_code(code):
    if not code:
        return code
    return re.sub(r"\s", "", str(code)).upper()


def is_valid_mpesa_code(code):
    return bool(code) and bool(_MPESA_CODE_RE.match(normalize_mpesa_code(code)))
