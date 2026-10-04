import {Plugin} from '@oclif/core'
import {readFileSync, writeFileSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const packageJson = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as {
  files?: string[]
}

if (!Array.isArray(packageJson.files)) {
  throw new TypeError('package.json must contain a files array to generate the oclif manifest')
}

const plugin = new Plugin({
  errorOnManifestCreate: true,
  ignoreManifest: true,
  respectNoCacheDefault: true,
  root,
  type: 'core',
})

await plugin.load()

const dotfile = packageJson.files.some((file) => file.endsWith('.oclif.manifest.json'))
const filename = join(root, `${dotfile ? '.' : ''}oclif.manifest.json`)
writeFileSync(filename, JSON.stringify(plugin.manifest, null, 2))
console.log(`wrote manifest to ${filename}`)
