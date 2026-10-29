# README screenshots

The five screenshots in the main README are captured by
`dashboard/tests/readme/screenshots.capture.ts` from a running deployment of a
released version. Nothing is mocked: the script signs in through the magic-link
flow, uploads three demo documents, remediates and approves one, downloads its
review evidence PDF and scans a Canvas demo course. Regenerate them for each
release so they do not drift from the product.

## Stack

Start `docker-compose.prod.yml` at the released version on an empty database,
with an override that publishes the API on `127.0.0.1:18300`, the dashboard on
`127.0.0.1:15373` and a Mailpit web UI on `127.0.0.1:18425`. Set
`PUBLIC_DASHBOARD_URL` and `CORS_ORIGINS` to `http://localhost:15373`,
`SMTP_HOST` to the Mailpit service, `LLM_PROVIDER=none` and
`OPEN_SIGNUP=false`. The first verified login becomes the administrator.

The counts in the screenshots depend on an empty database, so start from fresh
volumes each time.

## Canvas

The Canvas course view uses the OAuth developer key of the Canvas journey suite
(`tests/journeys`), whose redirect URI is
`http://localhost:18300/canvas/oauth/callback`. Add its client ID and secret as
`CANVAS_OAUTH_CLIENT_ID` and `CANVAS_OAUTH_CLIENT_SECRET`, with
`CANVAS_OAUTH_REDIRECT_URI` set to that URI and `CANVAS_OAUTH_ALLOWED_ORIGINS` to
the Canvas address.

Use a demo course that holds only generic files, taught by a teacher enrolled in
no other course. Do not use the CI journey course: its teacher must reach exactly
one course, and its name and files are not demo data.

Without `JOURNEY_CANVAS_URL` the script captures the other four screenshots and
skips this one.

## Run

The evidence page is rendered with `pdftoppm` from Poppler, which must be on the
`PATH`.

```bash
cd dashboard
JOURNEY_API_URL=http://localhost:18300 \
JOURNEY_MAIL_URL=http://localhost:18425 \
JOURNEY_ADMIN_EMAIL=accessibility.lead@example.edu \
JOURNEY_CANVAS_URL=https://canvas.example.edu \
JOURNEY_CANVAS_GATE_USERNAME=... JOURNEY_CANVAS_GATE_PASSWORD=... \
README_CANVAS_COURSE_ID=... \
README_CANVAS_TEACHER_LOGIN=... README_CANVAS_TEACHER_PASSWORD=... \
npx playwright test -c playwright.readme.config.ts
```

The gate variables are only needed when the Canvas instance sits behind HTTP
basic authentication. The screenshots are written to this folder, at 1440 pixels
wide in light mode. Check each one before committing it: no real institution,
staff or student names, no banners or notifications over the content, and no
page captured mid-animation.
