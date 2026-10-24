/* global process */

/**
 * Optional HTTP basic auth in front of the Canvas test instance. Aelira's own
 * Canvas sits behind one because its public source stopped receiving fixes; a
 * Canvas without a gate leaves both variables unset.
 *
 * The credentials are scoped to the Canvas origin, so the dashboard and API never
 * receive them, and are only sent when Canvas challenges for them: the LTI, OAuth
 * token, API and file endpoints are not gated and must not see a Basic header.
 */
export function canvasHttpCredentials() {
  const canvas = process.env.JOURNEY_CANVAS_URL;
  const username = process.env.JOURNEY_CANVAS_GATE_USERNAME;
  const password = process.env.JOURNEY_CANVAS_GATE_PASSWORD;
  if (!canvas || !username || !password) return undefined;
  return { username, password, origin: new URL(canvas).origin };
}
