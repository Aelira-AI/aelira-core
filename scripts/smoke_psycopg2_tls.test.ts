import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
