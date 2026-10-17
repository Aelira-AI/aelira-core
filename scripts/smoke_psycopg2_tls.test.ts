import { spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, test } from "bun:test";

test("a reused evidence directory cannot publish an earlier report", () => {
  const output = mkdtempSync(join(tmpdir(), "psycopg2-tls-reuse-test-"));
  const previousReport = '{"marker":"earlier-run"}\n';
  writeFileSync(join(output, "report.json"), previousReport);
  try {
    const probe = join(import.meta.dir, "smoke_psycopg2_tls.ts");
    const result = spawnSync(process.execPath, ["run", probe, output, "arm64"], {
      encoding: "utf8",
      env: { ...process.env, PATH: "" },
    });
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("evidence directory already exists");
    expect(readFileSync(join(output, "report.json"), "utf8")).toBe(
      previousReport,
    );
    expect(existsSync(join(output, "status.json"))).toBe(false);
  } finally {
    rmSync(output, { recursive: true });
  }
});

test("an older-image probe requires its matching verifier source", () => {
  const scratch = mkdtempSync(join(tmpdir(), "psycopg2-probe-pair-test-"));
  const probe = join(scratch, "smoke_psycopg2_runtime.py");
  copyFileSync(join(import.meta.dir, "smoke_psycopg2_runtime.py"), probe);
  try {
    const result = spawnSync(
      process.execPath,
      [
        "run", join(import.meta.dir, "smoke_psycopg2_tls.ts"),
        join(scratch, "output"), "arm64", "older-image:test", probe,
      ],
      { encoding: "utf8", env: { ...process.env, PATH: "" } },
    );
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("probe, verifier, and requirements must all exist");
    expect(existsSync(join(scratch, "output"))).toBe(false);
  } finally {
    rmSync(scratch, { recursive: true });
  }
});

test("an older-image probe requires reviewed requirements before Docker", () => {
  const scratch = mkdtempSync(join(tmpdir(), "psycopg2-requirements-test-"));
  const scripts = join(scratch, "scripts");
  mkdirSync(scripts);
  const probe = join(scripts, "smoke_psycopg2_runtime.py");
  copyFileSync(join(import.meta.dir, "smoke_psycopg2_runtime.py"), probe);
  copyFileSync(
    join(import.meta.dir, "verify_final_python_packages.py"),
    join(scripts, "verify_final_python_packages.py"),
  );
  try {
    const result = spawnSync(
      process.execPath,
      [
        "run", join(import.meta.dir, "smoke_psycopg2_tls.ts"),
        join(scratch, "output"), "arm64", "older-image:test", probe,
      ],
      { encoding: "utf8", env: { ...process.env, PATH: "" } },
    );
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("probe, verifier, and requirements must all exist");
    expect(existsSync(join(scratch, "output"))).toBe(false);
  } finally {
    rmSync(scratch, { recursive: true });
  }
});

if (process.env.AELIRA_DOCKER_LIFECYCLE_TEST === "1") {
  test("a failed database start removes only its labeled resources", () => {
    const realDocker = Bun.which("docker");
    if (!realDocker) throw new Error("Docker is required for the lifecycle test");
    const scratch = mkdtempSync(join(tmpdir(), "psycopg2-tls-lifecycle-test-"));
    const shim = join(scratch, "docker");
    const state = join(scratch, "created.json");
    const output = join(scratch, "output");
    const probe = join(import.meta.dir, "smoke_psycopg2_tls.ts");
    const image = process.env.AELIRA_TEST_IMAGE;
    const arch = process.arch === "arm64" ? "arm64" : "amd64";
    const arguments_ = ["run", probe, output, arch];
    if (image) {
      arguments_.push(image, join(import.meta.dir, "smoke_psycopg2_runtime.py"));
    }
    writeFileSync(
      shim,
      `#!${process.execPath}\n` +
        `import { spawnSync } from "node:child_process";\n` +
        `import { writeFileSync } from "node:fs";\n` +
        `const args = process.argv.slice(2);\n` +
        `const start = args[0] === "run" && args.includes("--detach");\n` +
        `const name = start ? args[args.indexOf("--name") + 1] : "";\n` +
        `if (start) {\n` +
        `  const image = args.findIndex((arg) => /^sha256:[a-f0-9]{64}$/.test(arg));\n` +
        `  if (image < 0) process.exit(90);\n` +
        `  args.splice(image, 0, "--entrypoint", "/aelira-absent-entrypoint");\n` +
        `}\n` +
        `const result = spawnSync(process.env.AELIRA_REAL_DOCKER, args, { stdio: "inherit" });\n` +
        `if (start) {\n` +
        `  const found = spawnSync(process.env.AELIRA_REAL_DOCKER, ["inspect", "--format", "{{json .}}", name], { encoding: "utf8" });\n` +
        `  if (found.status === 0) {\n` +
        `    const info = JSON.parse(found.stdout);\n` +
        `    writeFileSync(process.env.AELIRA_LIFECYCLE_STATE, JSON.stringify({\n` +
        `      name, label: info.Config.Labels["aelira.psycopg2-tls"],\n` +
        `      network: info.HostConfig.NetworkMode,\n` +
        `      volume: info.Mounts.find((mount) => mount.Type === "volume")?.Name,\n` +
        `      fixture: info.Mounts.find((mount) => mount.Destination === "/tls")?.Source,\n` +
        `    }));\n` +
        `  }\n` +
        `}\n` +
        `process.exit(result.status ?? 1);\n`,
      { mode: 0o700 },
    );
    chmodSync(shim, 0o700);
    try {
      const result = spawnSync(process.execPath, arguments_, {
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${scratch}:${process.env.PATH ?? ""}`,
          AELIRA_REAL_DOCKER: realDocker,
          AELIRA_LIFECYCLE_STATE: state,
        },
        timeout: 180_000,
      });
      expect(result.status).toBe(1);
      expect(existsSync(state)).toBe(true);
      const created = JSON.parse(readFileSync(state, "utf8")) as {
        name: string;
        label: string;
        network: string;
        volume: string;
        fixture: string;
      };
      expect(created.name.endsWith(created.label)).toBe(true);
      expect(created.volume).toBeTruthy();
      expect(created.fixture).toBeTruthy();
      const status = JSON.parse(readFileSync(join(output, "status.json"), "utf8"));
      expect(status).toMatchObject({
        passed: false,
        failure_stage: "isolated PostgreSQL",
      });
      expect(existsSync(join(output, "report.json"))).toBe(false);
      for (const args of [
        ["container", "inspect", created.name],
        ["network", "inspect", created.network],
        ["volume", "inspect", created.volume],
      ]) {
        expect(spawnSync(realDocker, args, { stdio: "ignore" }).status).not.toBe(0);
      }
      expect(existsSync(dirname(created.fixture))).toBe(false);
      if (process.env.AELIRA_LIFECYCLE_RECEIPT) {
        writeFileSync(
          process.env.AELIRA_LIFECYCLE_RECEIPT,
          JSON.stringify({
            passed: true,
            injected_database_name: created.name,
            injected_label: created.label,
            removed_container: true,
            removed_network: true,
            removed_anonymous_volume: true,
            removed_private_fixture: true,
            status,
          }) + "\n",
        );
      }
    } finally {
      rmSync(scratch, { recursive: true });
    }
  }, 180_000);
}
