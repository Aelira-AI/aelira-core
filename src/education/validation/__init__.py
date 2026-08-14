"""
PDF accessibility validation for compliance with PDF/UA (ISO 14289).

This package provides two complementary validators:

1. **MatterhornValidator** (native) - Checks ~15 core accessibility conditions
   using pikepdf to inspect PDF structure directly. Always available.

2. **VeraPDFValidator** (sidecar) - Checks 108 machine-checkable conditions
   via the veraPDF REST API container. Opt-in via VERAPDF_ENABLED=true.

Results from both validators can be merged into a unified compliance report
using VeraPDFValidator.merge_with_matterhorn().

Usage:
    from src.education.validation.matterhorn import MatterhornValidator
    from src.education.validation.verapdf import VeraPDFValidator

    mh = MatterhornValidator()
    mh_result = mh.validate("document.pdf")
    print(f"Matterhorn compliance: {mh_result.compliance_level}")

    vp = VeraPDFValidator()
    if vp.is_available():
        vp_result = vp.validate("document.pdf")
        merged = vp.merge_with_matterhorn(vp_result, mh_result)
        print(f"Overall compliant: {merged['summary']['overall_compliant']}")
"""
