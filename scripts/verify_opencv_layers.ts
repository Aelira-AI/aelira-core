#!/usr/bin/env bun
/** Reject published OpenCV's private native payload in every shipped layer. */

import { spawnSync } from "node:child_process";
import { closeSync, mkdtempSync, openSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function run(command: string, args: string[], outputFd?: number): string {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    maxBuffer: 128 * 1024 * 1024,
    stdio: outputFd === undefined
      ? ["ignore", "pipe", "pipe"]
      : ["ignore", outputFd, "pipe"],
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${command} failed: ${result.error ?? result.stderr ?? result.status}`);
  }
  return result.stdout ?? "";
}

function main(): void {
  if (process.argv.length !== 3 || !process.argv[2]) {
    throw new Error("usage: bun scripts/verify_opencv_layers.ts IMAGE");
  }
  const image = process.argv[2];
  const work = mkdtempSync(join(tmpdir(), "aelira-opencv-layers-"));
  try {
    const archive = join(work, "image.tar");
    const layerFile = join(work, "layer.tar");
    run("docker", ["save", "--output", archive, image]);
    const manifests = JSON.parse(run("tar", ["-xOf", archive, "manifest.json"]));
    if (!Array.isArray(manifests) || manifests.length !== 1 ||
        !Array.isArray(manifests[0].Layers) || manifests[0].Layers.length === 0) {
      throw new Error("docker save must contain one image with exported layers");
    }

    const cv2Extensions: string[] = [];
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
      for (const raw of run("tar", ["-tf", layerFile]).split("\n")) {
        const member = raw.replace(/^\.\//, "");
        if (/(^|\/)opencv_python(?:-[^/]*)?\.libs(\/|$)/i.test(member)) {
          throw new Error(`published OpenCV private library payload in layer ${layer}: ${member}`);
        }
        if (/(^|\/)lib(?:avcodec|avformat|avutil|swscale|ssl|crypto|tiff)[-_][0-9a-f]{8,}\.so(?:\.[0-9]+)*$/i.test(member)) {
          throw new Error(`wheel-private media or TLS library in layer ${layer}: ${member}`);
        }
        if (/^(?:opt\/venv|usr\/local)\/lib\/python3\.14\/site-packages\/cv2\/cv2[^/]*\.so$/.test(member)) {
          cv2Extensions.push(`${layer}:${member}`);
        }
      }
    }
    if (cv2Extensions.length !== 1) {
      throw new Error(`expected one source-built cv2 extension across all layers; found ${cv2Extensions.length}`);
    }
    console.log(`OpenCV layer payload valid: ${cv2Extensions[0]}`);
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
