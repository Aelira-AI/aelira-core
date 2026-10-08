# Workspace and transactional email presentation update

The public dashboard now uses the shared workspace design tokens and presentation components: welcome actions, typography, restrained card depth, account menu, summary layout, recent-scan table, and square/unclipped score ring. Guide and help links point to the working Aelira documentation. Cookie privacy links are underlined and configurable through `VITE_PRIVACY_POLICY_URL`; an unset value does not imply that the hosted Aelira policy covers a self-hosted deployment.

The port retains the public core's authentication, API client, notices, analytics policy and feature boundaries. Hosted Stripe subscriptions and paid feature gates are not part of this port.

Transactional email fragments use an Outlook-compatible table/inline-style shell and readable colours. Aelira defaults use the versioned tagline-free logo and Aelira AI Pty Ltd footer. Institution branding uses `BRAND_NAME`, `EMAIL_LOGO_URL`, `EMAIL_LEGAL_NAME`, `EMAIL_PRIVACY_URL`, `SUPPORT_EMAIL` and `PUBLIC_WEBSITE_URL` across the shell, subjects and body copy. A custom name without a logo uses a text identity; an unset legal name uses that custom name. Project-home fallback links are retained. See `BRANDING.md` for coordinated backend/dashboard configuration.

Local validation includes lint, TypeScript, production build, 403 dashboard unit tests, Chromium workspace/auth continuation tests, light/dark mobile/desktop axe checks, keyboard disclosure behaviour, reduced motion, zero/unverified scores, and focused email rendering tests. Automated accessibility checks are scoped; native Outlook delivery rendering remains to be verified.
