#!/usr/bin/env bun
/** Verify that a final image carries only the rebuilt CPython Expat pair. */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, mkdtempSync, openSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const oldHashes: Record<string, Set<string>> = {
  pyexpat: new Set([
    "4b02748c2413de67653f29d4fa9d2ebd61ef8193ab70b47a9a12e1cd326db869",
    "78d5661b82e9eeb2b4c707aa6dcce3d3837f43df8c3a366b584f0a04ddd5a090",
  ]),
  _elementtree: new Set([
    "e0ec5c24f86df24e09732be34908ae9aac64277e4f535d96320c1803a8be63bd",
    "0aeaca6d939c1c057ba77361921dac61b57e975a879be18ca9605a52f58b31b9",
  ]),
};
const extension = /^usr\/local\/lib\/python3\.14\/lib-dynload\/(pyexpat|_elementtree)\.cpython-314-[^/]+\.so$/;
const whiteout = /(^|\/)\.wh\.(pyexpat|_elementtree)\.cpython-314-/;

function run(command: string, args: string[], outputFd?: number): string {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    maxBuffer: 128 * 1024 * 1024,
    stdio: outputFd === undefined
      ? ["ignore", "pipe", "pipe"]
      : ["ignore", outputFd, "pipe"],
  });
  if (result.error || result.status !== 0) {
    throw new Error(
      `${command} ${args[0] ?? ""} failed: ${result.error ?? result.stderr ?? result.status}`,
    );
  }
  return result.stdout ?? "";
}

function memberBytes(archive: string, member: string): Buffer {
  const result = spawnSync("tar", ["-xOf", archive, member], {
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error || result.status !== 0 || !Buffer.isBuffer(result.stdout)) {
    throw new Error(`unable to read ${member} from exported image layer`);
  }
  return result.stdout;
}

function main(): void {
  if (process.argv.length !== 3 || !process.argv[2]) {
    throw new Error("usage: bun scripts/verify_python_expat_layers.ts IMAGE");
  }
  const image = process.argv[2];
  const work = mkdtempSync(join(tmpdir(), "aelira-expat-layers-"));
  try {
    const archive = join(work, "image.tar");
    const layerFile = join(work, "layer.tar");
    run("docker", ["save", "--output", archive, image]);
    const manifests = JSON.parse(run("tar", ["-xOf", archive, "manifest.json"]));
    if (!Array.isArray(manifests) || manifests.length !== 1 ||
        !Array.isArray(manifests[0].Layers) || manifests[0].Layers.length === 0) {
      throw new Error("docker save must contain one image with exported layers");
    }

    const found = new Map<string, { digest: string; path: string; layer: string }>();
    for (const layer of manifests[0].Layers) {
      if (typeof layer !== "string" || layer.startsWith("/") ||
          layer.split("/").includes("..")) {
        throw new Error("unsafe exported layer path");
      }
      const fd = openSync(layerFile, "w");
      try {
        run("tar", ["-xOf", archive, layer], fd);
      } finally {
        closeSync(fd);
      }
      const members = run("tar", ["-tf", layerFile]).split("\n");
      for (const raw of members) {
        const member = raw.replace(/^\.\//, "");
        if (whiteout.test(member)) {
          throw new Error(`Expat extension was whiteouted in shipped layer ${layer}`);
        }
        const match = extension.exec(member);
        if (!match) continue;
        const name = match[1];
        if (found.has(name)) {
          throw new Error(`multiple ${name} payloads occur across shipped layers`);
        }
        const bytes = memberBytes(layerFile, raw);
        const digest = createHash("sha256").update(bytes).digest("hex");
        if (oldHashes[name].has(digest) ||
            (name === "pyexpat" && bytes.includes(Buffer.from("expat_2.8.5")))) {
          throw new Error(`old bundled ${name} occurs in shipped layer ${layer}`);
        }
        found.set(name, { digest, path: `/${member}`, layer });
        console.log(`layer ${layer} ${name} ${digest}`);
      }
    }
    if (found.size !== 2 || !found.has("pyexpat") ||
        !found.has("_elementtree")) {
      throw new Error("expected one pyexpat and one _elementtree across all layers");
    }
    const pyexpat = found.get("pyexpat")!;
    const elementtree = found.get("_elementtree")!;
    if (pyexpat.layer !== elementtree.layer) {
      throw new Error("matching Expat extensions were copied in different layers");
    }
    const hashes = run("docker", [
      "run", "--rm", "--network", "none", "--entrypoint", "/usr/bin/sha256sum",
      image, pyexpat.path, elementtree.path,
    ]);
    const final = new Map(
      hashes.trim().split("\n").map((line) => {
        const match = /^([0-9a-f]{64})  (\/[^\n]+)$/.exec(line);
        if (!match) throw new Error("unexpected final-image sha256sum output");
        return [match[2], match[1]];
      }),
    );
    if (final.size !== 2 || final.get(pyexpat.path) !== pyexpat.digest ||
        final.get(elementtree.path) !== elementtree.digest) {
      throw new Error("exported Expat layer bytes differ from the final image");
    }
    console.log("Expat extension layers match the final image");
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
