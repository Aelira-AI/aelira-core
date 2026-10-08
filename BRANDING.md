# Branding and trademarks

## The short version

The **code** in this repository is AGPL-3.0. The **name "Aelira" and the Aelira logos are not**. Running, modifying, and redistributing the software is granted by the licence. Using the name or the marks to describe your version is not.

This is the usual arrangement for open-source projects with an identity, and it exists to protect users rather than to restrict you: when somebody downloads something called Aelira, they should be getting the thing this project actually maintains.

## What you may do

- Run, modify, self-host and redistribute the software under AGPL-3.0, including inside a commercial organisation.
- Say truthfully that your product is "built on Aelira Core", "a fork of Aelira Core", or "compatible with Aelira Core".
- Keep the bundled logos in place when running an unmodified or lightly-configured deployment. That is what they are there for.
- Replace the branding entirely. See below.

## What you may not do

- Call a modified version "Aelira", or name it in a way a reasonable person would confuse with Aelira.
- Use the Aelira logos as the identity of a different product, or as the marks of your fork.
- Imply endorsement, affiliation or certification by Aelira AI Pty Ltd.

If you are unsure whether a use is fine, ask. The answer is usually yes, and getting it in writing costs one email.

## Replacing the branding

Nothing here requires you to run this under our name. An institution deploying internally will often prefer its own, and that is supported directly rather than requiring a fork.

Dashboard, via Vite environment variables:

```bash
VITE_BRAND_NAME="Example University Accessibility"
VITE_LOGO_LIGHT="/branding/your-logo-light.svg"
VITE_LOGO_DARK="/branding/your-logo-dark.svg"
VITE_PRIVACY_POLICY_URL="https://example.edu/privacy"
```

Backend, for emails and accessibility evidence reports:

```bash
BRAND_NAME="Example University Accessibility"
PUBLIC_WEBSITE_URL="https://accessibility.example.edu"
SUPPORT_EMAIL="accessibility-help@example.edu"
FROM_NAME="Example University Accessibility"
EMAIL_LOGO_URL="https://accessibility.example.edu/static/email-logo.png"
EMAIL_LEGAL_NAME="Example University"
EMAIL_PRIVACY_URL="https://example.edu/privacy"
```

Use the same name for `BRAND_NAME` and `VITE_BRAND_NAME`. Transactional subjects, body text, sender-name fallback and the email shell use the backend identity. A custom brand without an email logo receives a text header rather than the Aelira mark. Set `EMAIL_LEGAL_NAME` to the actual operating institution; otherwise a custom brand uses its own name, while the Aelira default uses Aelira AI Pty Ltd. The email footer does not alter software copyright or licence notices.

Use an absolute public HTTPS URL to a PNG or JPEG for the email logo; dashboard SVG logos are not reliably supported by email clients. Logo/contact/home/privacy URLs belong to the operator. The dashboard displays a privacy link only when `VITE_PRIVACY_POLICY_URL` is configured; the email privacy link uses `EMAIL_PRIVACY_URL`.

Drop your own assets into `dashboard/public/` and point the variables at them. Favicons and app icons in that directory can be replaced in place. `SUPPORT_EMAIL` matters most: leave it unset and your users are given no contact at all, which is better than being sent to a support desk that has no access to your deployment and cannot help them.

## Why the code is AGPL

AGPL-3.0 was chosen deliberately. If you self-host for your own institution, including for thousands of staff and students, you owe nothing and no obligation is triggered. If you offer a modified version to others as a network service, your modifications have to be published under the same terms.

That is the arrangement that keeps a self-hosting university genuinely independent of the commercial service, which is the point of the open core.

## Contact

Trademark questions and permission requests: **hello@aelira.ai**
Security disclosures follow [SECURITY.md](SECURITY.md) instead.
