/** Isolated, synthetic TLS fixture for the final image's installed psycopg2. */

import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID, createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";

const image = process.argv[4] ?? "aelira-worker-isolation:test";
const postgresImage = "postgres:16";
const output = resolve(process.argv[2] ?? "test-results/psycopg2-runtime");
const expectedArch = process.argv[3] ?? "";
const probeSource = process.argv[5] ? resolve(process.argv[5]) : "";
if (!["amd64", "arm64"].includes(expectedArch)) {
  throw new Error("expected amd64 or arm64 as the second argument");
}
if (image !== "aelira-worker-isolation:test" && !probeSource) {
  throw new Error("an alternate image requires a read-only probe source mount");
}
mkdirSync(dirname(output), { recursive: true });
try {
  mkdirSync(output);
} catch (error) {
  if ((error as NodeJS.ErrnoException).code === "EEXIST") {
    console.error("psycopg2 TLS evidence directory already exists");
    process.exit(2);
  }
  throw error;
}

const run = (command: string, args: string[]): string =>
  execFileSync(command, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 120_000,
    maxBuffer: 10 * 1024 * 1024,
  }).trim();
const docker = (...args: string[]): string => run("docker", args);
const fixture = mkdtempSync(join(tmpdir(), "aelira-psycopg2-tls-"));
const nonce = randomUUID().slice(0, 12);
const label = `aelira.psycopg2-tls=${nonce}`;
const network = `aelira-psycopg2-tls-${nonce}`;
const database = `aelira-psycopg2-db-${nonce}`;
const server = join(fixture, "server");
const client = join(fixture, "client");
let stage = "setup";
let networkCreated = false;
let databaseCreated = false;
let passed = false;
const evidenceScope = probeSource
  ? "older-image-read-only-probe-control"
  : "native-final-image-ci";

function ownerFor(imageName: string, args: string[]): string {
  return docker(
    "run",
    "--rm",
    "--pull=never",
    "--network=none",
    "--entrypoint=/usr/bin/id",
    imageName,
    ...args,
  );
}

function chownInContainer(imageName: string, uid: string, path: string): void {
  docker(
    "run",
    "--rm",
    "--pull=never",
    "--network=none",
    "--user=root",
    "--mount",
    `type=bind,src=${fixture},dst=/fixture`,
    "--entrypoint=/bin/chown",
    imageName,
    `${uid}:${uid}`,
    `/fixture/${path}`,
  );
}

function assertOwnedContainer(): void {
  const labels = JSON.parse(
    docker("inspect", "--format", "{{json .Config.Labels}}", database),
  ) as Record<string, string>;
  if (labels["aelira.psycopg2-tls"] !== nonce) {
    throw new Error("database container ownership mismatch");
  }
  const mounts = JSON.parse(
    docker("inspect", "--format", "{{json .Mounts}}", database),
  ) as Array<{ Type: string; Source: string; Destination: string }>;
  for (const mount of mounts) {
    if (mount.Type === "bind" && !mount.Source.startsWith(fixture + sep)) {
      throw new Error("database container has an unexpected bind mount");
    }
    if (mount.Type === "volume" && mount.Destination !== "/var/lib/postgresql/data") {
      throw new Error("database container has an unexpected volume");
    }
  }
}

async function main(): Promise<void> {
  try {
    stage = "image identity";
    const imageId = docker("image", "inspect", image, "--format", "{{.Id}}");
    const imageArch = docker(
      "image",
      "inspect",
      image,
      "--format",
      "{{.Architecture}}",
    );
    if (imageArch !== expectedArch) {
      throw new Error("final image architecture mismatch");
    }
    docker("pull", postgresImage);
    const postgresId = docker(
      "image",
      "inspect",
      postgresImage,
      "--format",
      "{{.Id}}",
    );
    const postgresArch = docker(
      "image",
      "inspect",
      postgresImage,
      "--format",
      "{{.Architecture}}",
    );
    if (postgresArch !== expectedArch) {
      throw new Error("PostgreSQL image architecture mismatch");
    }

    stage = "synthetic credentials";
    mkdirSync(server, { mode: 0o755 });
    mkdirSync(client, { mode: 0o755 });
    chmodSync(fixture, 0o755);
    const password = randomBytes(32).toString("hex");
    writeFileSync(join(server, "password"), password, { mode: 0o400 });
    writeFileSync(join(client, "password"), password, { mode: 0o400 });
    const cert = join(server, "server.crt");
    const key = join(server, "server.key");
    run("openssl", [
      "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "2",
      "-subj", "/CN=psycopg2-smoke-db",
      "-addext", "subjectAltName=DNS:psycopg2-smoke-db",
      "-keyout", key, "-out", cert,
    ]);
    run("openssl", [
      "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "2",
      "-subj", "/CN=unrelated.invalid",
      "-addext", "subjectAltName=DNS:unrelated.invalid",
      "-keyout", join(server, "unrelated.key"),
      "-out", join(client, "unrelated.crt"),
    ]);
    copyFileSync(cert, join(client, "trusted.crt"));
    chmodSync(cert, 0o644);
    chmodSync(join(client, "trusted.crt"), 0o644);
    chmodSync(join(client, "unrelated.crt"), 0o644);
    chmodSync(key, 0o400);
    const pgUid = ownerFor(postgresImage, ["-u", "postgres"]);
    const clientUid = ownerFor(image, ["-u"]);
    chownInContainer(postgresImage, pgUid, "server/password");
    chownInContainer(postgresImage, pgUid, "server/server.key");
    chownInContainer(postgresImage, clientUid, "client/password");

    stage = "isolated PostgreSQL";
    docker("network", "create", "--internal", "--label", label, network);
    networkCreated = true;
    docker(
      "run", "--detach", "--pull=never", "--name", database,
      "--label", label, "--network", network,
      "--network-alias", "psycopg2-smoke-db",
      "--mount", `type=bind,src=${server},dst=/tls,readonly`,
      "--env", "POSTGRES_USER=aelira",
      "--env", "POSTGRES_DB=aelira_smoke",
      "--env", "POSTGRES_PASSWORD_FILE=/tls/password",
      postgresImage, "postgres", "-c", "ssl=on",
      "-c", "ssl_cert_file=/tls/server.crt",
      "-c", "ssl_key_file=/tls/server.key",
    );
    databaseCreated = true;
    let ready = false;
    for (let attempt = 0; attempt < 60; attempt++) {
      try {
        docker(
          "exec", database, "pg_isready", "-h", "127.0.0.1",
          "-U", "aelira", "-d", "aelira_smoke",
        );
        ready = true;
        break;
      } catch {
        await Bun.sleep(2_000);
      }
    }
    if (!ready) throw new Error("synthetic PostgreSQL did not become ready");
    const networks = JSON.parse(
      docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", database),
    ) as Record<string, { IPAddress: string }>;
    const address = networks[network]?.IPAddress;
    if (!address) throw new Error("synthetic PostgreSQL address is missing");

    stage = "final-image TLS and SQL controls";
    const result = docker(
      "run", "--rm", "--pull=never", "--network", network,
      "--read-only", "--tmpfs", "/tmp:rw,nosuid,mode=1777",
      ...(probeSource ? ["--mount", `type=bind,src=${probeSource},dst=/app/scripts/smoke_psycopg2_runtime.py,readonly`] : []),
      "--mount", `type=bind,src=${join(client, "password")},dst=/run/secrets/pg_password,readonly`,
      "--mount", `type=bind,src=${join(client, "trusted.crt")},dst=/run/certs/trusted.crt,readonly`,
      "--mount", `type=bind,src=${join(client, "unrelated.crt")},dst=/run/certs/unrelated.crt,readonly`,
      "--env", "AELIRA_PG_HOST=psycopg2-smoke-db",
      "--env", `AELIRA_PG_HOSTADDR=${address}`,
      "--env", "AELIRA_PG_USER=aelira",
      "--env", "AELIRA_PG_DATABASE=aelira_smoke",
      "--env", "AELIRA_PG_PASSWORD_FILE=/run/secrets/pg_password",
      "--env", "AELIRA_PG_CA_FILE=/run/certs/trusted.crt",
      "--env", "AELIRA_PG_UNTRUSTED_CA_FILE=/run/certs/unrelated.crt",
      "--env", `AELIRA_RUNTIME_IMAGE_ID=${imageId}`,
      "--env", `AELIRA_EXPECT_ARCH=${expectedArch}`,
      "--entrypoint", "/opt/venv/bin/python", image,
      "/app/scripts/smoke_psycopg2_runtime.py",
    );
    const receipt = JSON.parse(result) as Record<string, unknown>;
    receipt.evidence_scope = evidenceScope;
    receipt.probe_source_sha256 = createHash("sha256")
      .update(readFileSync(probeSource || "scripts/smoke_psycopg2_runtime.py"))
      .digest("hex");
    receipt.postgres_image_id = postgresId;
    receipt.tls_server_cert_sha256 = createHash("sha256")
      .update(readFileSync(cert))
      .digest("hex");
    writeFileSync(join(output, "report.json"), JSON.stringify(receipt, null, 2) + "\n");
    passed = true;
  } finally {
    let cleanupFailed = false;
    if (databaseCreated) {
      try {
        assertOwnedContainer();
        docker("rm", "--force", "--volumes", database);
      } catch {
        cleanupFailed = true;
      }
    }
    if (networkCreated) {
      try {
        const labels = JSON.parse(
          docker("network", "inspect", "--format", "{{json .Labels}}", network),
        ) as Record<string, string>;
        if (labels["aelira.psycopg2-tls"] !== nonce) {
          throw new Error("synthetic network ownership mismatch");
        }
        docker("network", "rm", network);
      } catch {
        cleanupFailed = true;
      }
    }
    if (!cleanupFailed) rmSync(fixture, { recursive: true, force: true });
    writeFileSync(
      join(output, "status.json"),
      JSON.stringify({
        passed: passed && !cleanupFailed,
        evidence_scope: evidenceScope,
        ...(passed && !cleanupFailed
          ? {}
          : { failure_stage: cleanupFailed ? "cleanup" : stage }),
      }) + "\n",
    );
    if (cleanupFailed) {
      stage = "cleanup";
      throw new Error("owned synthetic Docker cleanup failed");
    }
  }
}

main().catch(() => {
  console.error(`psycopg2 final-image TLS acceptance failed during ${stage}`);
  process.exitCode = 1;
});
