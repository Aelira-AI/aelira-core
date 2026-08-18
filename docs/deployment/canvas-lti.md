# Canvas LTI administrator configuration

Aelira's Canvas integration uses an LTI 1.3 Developer Key. Generate the
configuration from the running API instead of maintaining a separate JSON copy:

1. Open `GET https://<api-host>/lti/config`.
2. In Canvas, open **Admin → Developer Keys → + Developer Key → LTI Key**.
3. Choose **Paste JSON**, paste the response, save the key, and enable it.
4. Install the key only in the intended account or sub-account.

## Required placement checks

Confirm the installed configuration has:

- **Course Navigation**: visibility `Admins`.
- **Account Navigation**: visibility `Admins` and disabled by default unless the
  institution intends to expose the account-wide overview.

Canvas does not expose the same navigation-visibility control for every deep-link
placement. `assignment_selection` and `editor_button` therefore remain protected
by Aelira's server-side launch authorization rather than a placement claim.

Placement visibility is defense in depth. It controls where Canvas offers a
supported navigation link; it is not Aelira's authorization boundary. Aelira
independently validates the signed launch and permits only Administrator,
Instructor, TeachingAssistant, and ContentDeveloper roles. Learner, Student,
Mentor, Observer, missing, and unknown roles are denied before provisioning or
other launch side effects.

## Required course field

The generated Developer Key JSON must contain:

```json
"canvas_course_id": "$Canvas.course.id"
```

Canvas may display this manual custom-field label with a `custom_` prefix, such as
`custom_canvas_course_id`. In an LTI 1.3 launch token, however, the member inside
`https://purl.imsglobal.org/spec/lti/claim/custom` is the unprefixed
`canvas_course_id` key.

This field is required because the standard LTI `context.id` is opaque and is not
necessarily the numeric Canvas REST API course ID. After installation, perform an
Instructor launch and confirm diagnostics show a substituted numeric
`canvas_course_id`, not the literal `$Canvas.course.id` expression.

## Reserved resource-link metadata

The generated JSON also contains:

```json
"canvas_resource_link_id": "$ResourceLink.id"
```

Canvas may display the manual field as `custom_canvas_resource_link_id`; the LTI
1.3 custom-claim member remains `canvas_resource_link_id`.

This value is reserved for future item-level routing metadata. The current
release does not use it to open a Canvas page, assignment, file, module item, or
other item-level deep link. Do not present item-level navigation as supported.

## Installation verification

Before enabling the integration for users:

1. Launch from Course Navigation as an Instructor and confirm the correct course.
2. Launch from Account Navigation as an Administrator and confirm the overview.
3. Attempt a Learner launch and confirm a neutral HTTP 403 response.
4. Confirm the denied launch did not provision a user or create launch, grade,
   deep-link, or statistics side effects.
5. Confirm the decoded signed launch has `canvas_course_id` in the LTI custom
   claim and that its value is the expected numeric Canvas course ID.

## Canvas OAuth operator trust boundary

Canvas account connection installs a deployment-wide OAuth client secret at a
specific Canvas origin. In staging and production, operators **must** set
`CANVAS_OAUTH_ALLOWED_ORIGINS` to the Canvas instances this deployment trusts:

```env
CANVAS_OAUTH_ALLOWED_ORIGINS=https://canvas.university.edu
```

Use a comma-separated list only when the deployment intentionally serves more
than one Canvas instance. Every entry must be a canonical HTTPS root origin
(scheme, hostname, and optional non-default port only). A request is authorized
only by an exact canonical-origin match. Wildcards, subdomain/suffix matching,
paths, credentials, redirects, and HTTP origins are not accepted. Development
and test may omit the setting for explicit localhost and test fixtures.

The connect route validates the requested origin against this operator policy
before state issuance or outbound work. The callback consumes one-time state and
checks its stored origin against the current allowlist again, so removing an
origin immediately prevents pending callbacks from exchanging secrets or
writing credentials. Production and staging continue to require Redis-backed
state; the in-memory fallback remains development/test only.

This is the network trust model: operators allowlist only their institution's
Canvas instance, and outbound HTTPS uses normal strict certificate validation
for that exact hostname. Public DNS routability alone is not authorization.
Exact operator authorization plus TLS hostname/certificate verification is the
DNS-rebinding boundary; do not replace the hostname with a resolved raw IP or
disable certificate verification.

Connecting or disconnecting Canvas credentials is account management. Non-LTI
callers must be Aelira `ADMIN` or `SUPER_ADMIN` (the development mock admin is
also accepted). LTI callers must carry Canvas's authoritative, account-wide
`Administrator` assertion. Faculty, course-scoped LTI staff, API keys owned by
faculty, and faculty sessions cannot connect or disconnect. Connection status
remains read-only for authenticated users within their department.
