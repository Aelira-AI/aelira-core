/**
 * Configuration utility for Aelira CLI
 * Manages ~/.aelira/config.json for API settings and profiles
 */

import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'

import { ApiClient, ApiConnectionError, ApiError } from './api-client.js'

export interface AeliraConfig {
  activeProfile: string
  profiles: Record<string, ProfileConfig>
  version: string
}

export interface ProfileConfig {
  apiKey?: string
  apiUrl: string
  department?: string
  name: string
}

// Lazy resolution for test isolation via AELIRA_CONFIG_DIR env var
function resolveConfigDir(): string {
  return process.env.AELIRA_CONFIG_DIR ?? path.join(os.homedir(), '.aelira')
}

function resolveConfigFile(): string {
  return path.join(resolveConfigDir(), 'config.json')
}

const CONFIG_VERSION = '1.0.0'

// Exported (read-only in intent) so tests can assert it is never mutated by
// readConfig()/setConfigValue() — see the regression test in config.test.ts.
export const DEFAULT_CONFIG: AeliraConfig = {
  activeProfile: 'default',
  profiles: {
    default: {
      apiUrl: 'http://localhost:8000',
      name: 'Default',
    },
  },
  version: CONFIG_VERSION,
}

/**
 * Get the config directory path
 */
export function getConfigDir(): string {
  return resolveConfigDir()
}

/**
 * Get the config file path
 */
export function getConfigPath(): string {
  return resolveConfigFile()
}

/**
 * Check if config file exists
 */
export async function configExists(): Promise<boolean> {
  try {
    await fs.access(resolveConfigFile())
    return true
  } catch {
    return false
  }
}

/**
 * Read the config file, returns default config if not found
 */
export async function readConfig(): Promise<AeliraConfig> {
  try {
    const data = await fs.readFile(resolveConfigFile(), 'utf8')
    const config = JSON.parse(data) as AeliraConfig
    // Ensure required fields exist. Deep-copy DEFAULT_CONFIG here: a shallow
    // spread leaves `profiles` (and any profile objects merged in from it) as
    // shared references to the module-level default, so later in-place
    // mutation (e.g. setConfigValue's `config.profiles[activeProfile].apiKey = value`)
    // would corrupt DEFAULT_CONFIG for the rest of the process lifetime.
    const defaults = structuredClone(DEFAULT_CONFIG)
    return {
      ...defaults,
      ...config,
      profiles: {
        ...defaults.profiles,
        ...config.profiles,
      },
    }
  } catch {
    return structuredClone(DEFAULT_CONFIG)
  }
}

/**
 * Write the config file
 */
export async function writeConfig(config: AeliraConfig): Promise<void> {
  // Ensure directory exists
  await fs.mkdir(resolveConfigDir(), { recursive: true })
  await fs.writeFile(resolveConfigFile(), JSON.stringify(config, null, 2))
}

/**
 * Get a config value from the active profile
 */
export async function getConfigValue(key: keyof ProfileConfig): Promise<string | undefined> {
  const config = await readConfig()
  const profile = config.profiles[config.activeProfile] || config.profiles.default
  return profile[key] as string | undefined
}

/**
 * Set a config value in the active profile
 */
export async function setConfigValue(key: keyof ProfileConfig, value: string): Promise<void> {
  const config = await readConfig()
  const activeProfile = config.activeProfile || 'default'

  if (!config.profiles[activeProfile]) {
    config.profiles[activeProfile] = {
      apiUrl: 'http://localhost:8000',
      name: activeProfile,
    }
  }

  // Type-safe assignment
  switch (key) {
  case 'apiKey': {
    config.profiles[activeProfile].apiKey = value
  
  break;
  }

  case 'apiUrl': {
    config.profiles[activeProfile].apiUrl = value
  
  break;
  }

  case 'department': {
    config.profiles[activeProfile].department = value
  
  break;
  }

  case 'name': {
    config.profiles[activeProfile].name = value
  
  break;
  }
  // No default
  }

  await writeConfig(config)
}

/**
 * Get the active profile configuration
 */
export async function getActiveProfile(): Promise<ProfileConfig> {
  const config = await readConfig()
  return config.profiles[config.activeProfile] || config.profiles.default
}

/**
 * Set the active profile
 */
export async function setActiveProfile(profileName: string): Promise<void> {
  const config = await readConfig()
  if (!config.profiles[profileName]) {
    throw new Error(`Profile "${profileName}" does not exist`)
  }

  config.activeProfile = profileName
  await writeConfig(config)
}

/**
 * Create a new profile
 */
export async function createProfile(name: string, profileConfig: Partial<ProfileConfig>): Promise<void> {
  const config = await readConfig()
  if (config.profiles[name]) {
    throw new Error(`Profile "${name}" already exists`)
  }

  config.profiles[name] = {
    apiUrl: profileConfig.apiUrl || 'http://localhost:8000',
    name: profileConfig.name || name,
    ...(profileConfig.apiKey && { apiKey: profileConfig.apiKey }),
    ...(profileConfig.department && { department: profileConfig.department }),
  }
  await writeConfig(config)
}

/**
 * Delete a profile
 */
export async function deleteProfile(name: string): Promise<void> {
  if (name === 'default') {
    throw new Error('Cannot delete the default profile')
  }

  const config = await readConfig()
  if (!config.profiles[name]) {
    throw new Error(`Profile "${name}" does not exist`)
  }

  delete config.profiles[name]
  if (config.activeProfile === name) {
    config.activeProfile = 'default'
  }

  await writeConfig(config)
}

/**
 * List all profiles
 */
export async function listProfiles(): Promise<Array<{ active: boolean; config: ProfileConfig; name: string; }>> {
  const config = await readConfig()
  return Object.entries(config.profiles).map(([name, profileConfig]) => ({
    active: name === config.activeProfile,
    config: profileConfig,
    name,
  }))
}

/**
 * Resolve the API URL using the CLI-wide precedence contract.
 */
export async function getApiUrl(explicitApiUrl?: string): Promise<string> {
  if (explicitApiUrl) {
    return explicitApiUrl
  }

  if (process.env.AELIRA_API_URL) {
    return process.env.AELIRA_API_URL
  }

  const profile = await getActiveProfile()
  return profile?.apiUrl || DEFAULT_CONFIG.profiles.default.apiUrl
}

/**
 * Get the API key, with environment variable override support
 */
export async function getApiKey(): Promise<string | undefined> {
  // Environment variable takes precedence
  if (process.env.AELIRA_API_KEY) {
    return process.env.AELIRA_API_KEY
  }

  const profile = await getActiveProfile()
  return profile.apiKey
}

/**
 * Get the department, with environment variable override support
 */
export async function getDepartment(): Promise<string> {
  // Environment variable takes precedence
  if (process.env.AELIRA_DEPARTMENT) {
    return process.env.AELIRA_DEPARTMENT
  }

  const profile = await getActiveProfile()
  return profile.department || 'default'
}

/**
 * Validate API connection
 */
export async function validateConnection(apiUrl?: string): Promise<{ details?: any; message: string; success: boolean; }> {
  try {
    const api = new ApiClient({ apiUrl })
    const response = await api.get('/health', { timeout: 10_000, retry: false })
    const data = await response.json()
    const url = apiUrl || await getApiUrl()
    return { details: data, message: `Connected to Aelira API at ${url}`, success: true }
  } catch (error: any) {
    const url = apiUrl || await getApiUrl()
    if (error instanceof ApiError) {
      return { message: `API returned status ${error.status}`, success: false }
    }

    if (error instanceof ApiConnectionError) {
      if (error.message.includes('timed out')) {
        return { message: `Connection timed out after 10 seconds`, success: false }
      }

      return { message: `Could not connect to ${url}: ${error.message}`, success: false }
    }

    return { message: `Could not connect to ${url}: ${error.message}`, success: false }
  }
}

/**
 * Initialize config with default values (creates config file if not exists)
 */
export async function initializeConfig(): Promise<boolean> {
  const exists = await configExists()
  if (!exists) {
    await writeConfig(DEFAULT_CONFIG)
    return true // Created new config
  }

  return false // Config already exists
}
