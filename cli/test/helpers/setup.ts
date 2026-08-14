import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'

/**
 * Create an isolated temp directory for test config files.
 */
export async function createTestDir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), 'aelira-test-'))
}

/**
 * Remove a test directory and all its contents.
 */
export async function cleanTestDir(dir: string): Promise<void> {
  await fs.rm(dir, { force: true, recursive: true })
}

/**
 * Point AELIRA_CONFIG_DIR at the given temp directory.
 * Returns a cleanup function that restores the original value.
 */
export function withTestConfig(dir: string): () => void {
  const original = process.env.AELIRA_CONFIG_DIR
  process.env.AELIRA_CONFIG_DIR = dir
  return () => {
    if (original === undefined) {
      delete process.env.AELIRA_CONFIG_DIR
    } else {
      process.env.AELIRA_CONFIG_DIR = original
    }
  }
}
