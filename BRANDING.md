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
```

Backend, for emails, reports and certificates:

```bash
BRAND_NAME="Example University Accessibility"
PUBLIC_WEBSITE_URL="https://accessibility.example.edu"
SUPPORT_EMAIL="accessibility-help@example.edu"
```

Drop your own assets into `dashboard/public/` and point the variables at them. Favicons and app icons in that directory can be replaced in place. `SUPPORT_EMAIL` matters most: leave it unset and your users are given no contact at all, which is better than being sent to a support desk that has no access to your deployment and cannot help them.

## Why the code is AGPL

AGPL-3.0 was chosen deliberately. If you self-host for your own institution, including for thousands of staff and students, you owe nothing and no obligation is triggered. If you offer a modified version to others as a network service, your modifications have to be published under the same terms.

That is the arrangement that keeps a self-hosting university genuinely independent of the commercial service, which is the point of the open core.

## Contact

Trademark questions and permission requests: **hello@aelira.ai**
Security disclosures follow [SECURITY.md](SECURITY.md) instead.
