# Workspace and transactional email presentation update

The public dashboard now uses the shared workspace design tokens and presentation components: welcome actions, typography, restrained card depth, account menu, summary layout, recent-scan table, and square/unclipped score ring. Guide and help links point to the working Aelira documentation. Cookie privacy links are underlined and configurable through `VITE_PRIVACY_POLICY_URL`.

The port retains the public core's authentication, API client, notices, analytics policy and feature boundaries. Hosted Stripe subscriptions and paid feature gates are not part of this port.

Transactional email fragments use an Outlook-compatible table/inline-style shell, readable colours, a versioned tagline-free logo, and a dynamic Aelira AI Pty Ltd footer. Operators can override `EMAIL_LOGO_URL` and `EMAIL_LEGAL_NAME`; project-home fallback links are retained.

Local validation includes lint, TypeScript, production build, 403 dashboard unit tests, Chromium workspace/auth continuation tests, light/dark mobile/desktop axe checks, keyboard disclosure behaviour, reduced motion, zero/unverified scores, and focused email rendering tests. Automated accessibility checks are scoped; native Outlook delivery rendering remains to be verified.
