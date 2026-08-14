import type { Browser, Page } from 'playwright'

import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { chromium } from 'playwright'

// Color Vision Deficiency Types
const CVD_TYPES = {
  achromatopsia: { description: 'Complete color blindness (grayscale)', name: 'Achromatopsia', population: 0.003 },
  deuteranomaly: { description: 'Green-weak (most common, 5% males)', name: 'Deuteranomaly', population: 5 },
  deuteranopia: { description: 'Green-blind (1% males)', name: 'Deuteranopia', population: 1 },
  protanomaly: { description: 'Red-weak (1% males)', name: 'Protanomaly', population: 1 },
  protanopia: { description: 'Red-blind (1% males)', name: 'Protanopia', population: 1 },
  tritanomaly: { description: 'Blue-weak (rare)', name: 'Tritanomaly', population: 0.01 },
  tritanopia: { description: 'Blue-blind (rare)', name: 'Tritanopia', population: 0.01 },
} as const

type CVDType = keyof typeof CVD_TYPES

// Transformation matrices for CVD simulation (Brettel, Viénot, & Mollon 1997)
const CVD_MATRICES: Record<string, number[][]> = {
  achromatopsia: [
    [0.299, 0.587, 0.114],
    [0.299, 0.587, 0.114],
    [0.299, 0.587, 0.114],
  ],
  deuteranopia: [
    [0.367_322, 0.860_646, -0.227_968],
    [0.280_085, 0.672_501, 0.047_413],
    [-0.011_82, 0.042_94, 0.968_881],
  ],
  protanopia: [
    [0.152_286, 1.052_583, -0.204_868],
    [0.114_503, 0.786_281, 0.099_216],
    [-0.003_882, -0.048_116, 1.051_998],
  ],
  tritanopia: [
    [1.255_528, -0.076_749, -0.178_779],
    [-0.078_411, 0.930_809, 0.147_602],
    [0.004_733, 0.691_367, 0.3039],
  ],
}

interface ColorPair {
  background: string
  contrastRatio: number
  element: string
  foreground: string
  selector: string
  textContent?: string
}

interface CVDIssue {
  affectedPopulation: number
  cvdType: string
  description: string
  element: string
  originalContrast: number
  selector: string
  severity: string
  simulatedContrast: number
  suggestedFix: string
}

interface CVDResult {
  colorPairsAnalyzed: number
  complianceScore: number
  issues: CVDIssue[]
  totalAffectedPopulation: number
  url: string
  wcagCompliant: boolean
}

export default class Cvd extends Command {
  static args = {
    target: Args.string({
      description: 'URL or HTML file to analyze for color vision deficiency accessibility',
      required: true,
    }),
  }
static description =
    'Analyze color accessibility for color-blind users (8% of males) - simulates protanopia, deuteranopia, tritanopia and more'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com',
    '<%= config.bin %> <%= command.id %> https://example.com --type protanopia',
    '<%= config.bin %> <%= command.id %> ./index.html --all-types --format json',
  ]
static flags = {
    'all-types': Flags.boolean({
      default: false,
      description: 'Test all 7 CVD types (slower but comprehensive)',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    'min-contrast': Flags.string({
      default: '4.5',
      description: 'Minimum contrast ratio threshold (WCAG AA=4.5, AAA=7.0)',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON format)',
    }),
    timeout: Flags.integer({
      char: 't',
      default: 30_000,
      description: 'Page load timeout in milliseconds',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
    type: Flags.string({
      description: 'Specific CVD type to test (protanopia, deuteranopia, tritanopia, deuteranomaly, protanomaly, tritanomaly, achromatopsia)',
      options: Object.keys(CVD_TYPES),
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Cvd)
    const startTime = Date.now()

    intro('Aelira CLI - Color Vision Deficiency Analyzer')

    let browser: Browser | null = null
    let page: null | Page = null

    try {
      const s = spinner()
      s.start('Launching browser...')

      browser = await chromium.launch({ headless: true })
      const context = await browser.newContext()
      page = await context.newPage()

      s.message('Navigating to ' + args.target + '...')

      // Handle file:// URLs for local HTML files
      let targetUrl = args.target
      if (!args.target.startsWith('http')) {
        const absolutePath = path.resolve(args.target)
        await fs.access(absolutePath)
        targetUrl = `file://${absolutePath}`
      }

      await page.goto(targetUrl, { timeout: flags.timeout })

      s.message('Extracting color pairs from page...')
      const analysisStart = Date.now()

      // Extract color pairs from the page
      const colorPairs = await this.extractColorPairs(page)

      // Determine which CVD types to test
      let cvdTypesToTest: CVDType[]
      if (flags.type) {
        cvdTypesToTest = [flags.type as CVDType]
      } else if (flags['all-types']) {
        cvdTypesToTest = Object.keys(CVD_TYPES) as CVDType[]
      } else {
        // Default: test most common types (affects ~8% of males)
        cvdTypesToTest = ['protanopia', 'deuteranopia', 'deuteranomaly']
      }

      s.message(`Analyzing ${colorPairs.length} color pairs for ${cvdTypesToTest.length} CVD types...`)

      // Analyze color pairs for CVD issues
      const minContrast = Number.parseFloat(flags['min-contrast'])
      const issues = this.analyzeColorPairs(colorPairs, cvdTypesToTest, minContrast)

      // Calculate metrics
      const totalAffectedPopulation = this.calculateAffectedPopulation(issues)
      const complianceScore = this.calculateComplianceScore(colorPairs.length, issues)
      const wcagCompliant = complianceScore >= 80 && !issues.some((i) => i.severity === 'critical')

      const analysisDuration = Date.now() - analysisStart
      s.stop(`Analysis complete in ${analysisDuration}ms`)

      const result: CVDResult = {
        colorPairsAnalyzed: colorPairs.length,
        complianceScore,
        issues,
        totalAffectedPopulation,
        url: args.target,
        wcagCompliant,
      }

      // Output results
      if (flags.format === 'json') {
        const output = {
          ...result,
          cvdTypesAnalyzed: cvdTypesToTest,
          minContrastThreshold: minContrast,
          performance: {
            analysis_time: analysisDuration,
            total_time: Date.now() - startTime,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ CVD analysis report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displayResults(result, cvdTypesToTest, minContrast, analysisDuration)
        outro(wcagCompliant ? '✅ Color accessibility: Good for color-blind users' : '⚠️  Color accessibility: Issues found for color-blind users')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    } finally {
      if (page) await page.close()
      if (browser) await browser.close()
    }
  }

  private analyzeColorPairs(colorPairs: ColorPair[], cvdTypes: CVDType[], minContrast: number): CVDIssue[] {
    const issues: CVDIssue[] = []

    for (const pair of colorPairs) {
      for (const cvdType of cvdTypes) {
        // Simulate colors for this CVD type
        const simFg = this.simulateCVD(pair.foreground, cvdType)
        const simBg = this.simulateCVD(pair.background, cvdType)

        // Calculate simulated contrast
        const simulatedContrast = this.calculateContrastRatio(simFg, simBg)

        // Check if it fails the minimum contrast threshold
        if (simulatedContrast < minContrast) {
          const cvdInfo = CVD_TYPES[cvdType]
          const severity = simulatedContrast < 3 ? 'critical' : simulatedContrast < 4.5 ? 'serious' : 'moderate'

          issues.push({
            affectedPopulation: cvdInfo.population,
            cvdType: cvdInfo.name,
            description: `Color pair fails WCAG contrast for ${cvdInfo.name} users`,
            element: pair.element,
            originalContrast: Math.round(pair.contrastRatio * 100) / 100,
            selector: pair.selector,
            severity,
            simulatedContrast: Math.round(simulatedContrast * 100) / 100,
            suggestedFix: this.suggestFix(cvdType, simulatedContrast),
          })
        }
      }
    }

    return issues
  }

  private calculateAffectedPopulation(issues: CVDIssue[]): number {
    // Get unique CVD types from issues and sum their populations
    const affectedTypes = new Set(issues.map((i) => i.cvdType))
    let total = 0

    for (const typeName of affectedTypes) {
      const typeEntry = Object.entries(CVD_TYPES).find(([, v]) => v.name === typeName)
      if (typeEntry) {
        total += typeEntry[1].population
      }
    }

    return Math.round(total * 100) / 100
  }

  private calculateComplianceScore(totalPairs: number, issues: CVDIssue[]): number {
    if (totalPairs === 0) return 100

    // Unique elements with issues
    const elementsWithIssues = new Set(issues.map((i) => i.selector)).size
    const passRate = ((totalPairs - elementsWithIssues) / totalPairs) * 100

    // Deduct for critical issues
    const criticalCount = issues.filter((i) => i.severity === 'critical').length
    const deduction = criticalCount * 5

    return Math.max(0, Math.min(100, Math.round(passRate - deduction)))
  }

  private calculateContrastRatio(color1: string, color2: string): number {
    const rgb1 = this.hexToRgb(color1)
    const rgb2 = this.hexToRgb(color2)

    const l1 = this.calculateRelativeLuminance(rgb1)
    const l2 = this.calculateRelativeLuminance(rgb2)

    const lighter = Math.max(l1, l2)
    const darker = Math.min(l1, l2)

    return (lighter + 0.05) / (darker + 0.05)
  }

  private calculateRelativeLuminance(rgb: [number, number, number]): number {
    const [r, g, b] = rgb.map((c) => {
      const normalized = c / 255
      return normalized <= 0.039_28 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
    })

    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }

  private displayResults(result: CVDResult, cvdTypes: CVDType[], minContrast: number, analysisTime: number): void {
    // Header
    this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  Color Vision Deficiency Analysis Report')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  URL: ${result.url}`)
    this.log(`  Color Pairs Analyzed: ${result.colorPairsAnalyzed}`)
    this.log(`  CVD Types Tested: ${cvdTypes.map((t) => CVD_TYPES[t].name).join(', ')}`)
    this.log(`  Minimum Contrast Threshold: ${minContrast}:1 (WCAG ${minContrast >= 7 ? 'AAA' : 'AA'})`)
    this.log(`  Compliance Score: ${this.getScoreDisplay(result.complianceScore)}`)
    this.log(`  Analysis Time: ${analysisTime}ms\n`)

    // Population Impact
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  👥 Population Impact')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (result.issues.length > 0) {
      this.log(`  ${pc.yellow(`~${result.totalAffectedPopulation}% of males`)} may have difficulty with some colors`)
      this.log(`  ${pc.dim('(8% of males have some form of color blindness)')}\n`)
    } else {
      this.log(`  ${pc.green('Colors are accessible')} to all tested CVD types\n`)
    }

    // Issues
    if (result.issues.length > 0) {
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log(`  ⚠️  Issues Found (${result.issues.length})`)
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

      // Group issues by element
      const issuesByElement = new Map<string, CVDIssue[]>()
      for (const issue of result.issues) {
        const key = issue.selector
        if (!issuesByElement.has(key)) {
          issuesByElement.set(key, [])
        }

        issuesByElement.get(key)!.push(issue)
      }

      let displayCount = 0
      for (const [selector, elementIssues] of issuesByElement) {
        if (displayCount >= 10) {
          this.log(`  ... and ${issuesByElement.size - 10} more elements with issues\n`)
          break
        }

        const firstIssue = elementIssues[0]
        const severityIcon = this.getSeverityIcon(firstIssue.severity)

        this.log(`  ${severityIcon} <${firstIssue.element}> ${pc.dim(selector.slice(0, 40))}`)
        this.log(`     Original contrast: ${firstIssue.originalContrast}:1`)

        for (const issue of elementIssues.slice(0, 3)) {
          const contrastColor = issue.simulatedContrast < 3 ? pc.red : issue.simulatedContrast < 4.5 ? pc.yellow : pc.dim
          this.log(`     ${issue.cvdType}: ${contrastColor(`${issue.simulatedContrast}:1`)}`)
        }

        if (elementIssues.length > 3) {
          this.log(`     ... and ${elementIssues.length - 3} more CVD types`)
        }

        this.log(`     💡 ${firstIssue.suggestedFix}`)
        this.log('')

        displayCount++
      }
    } else {
      this.log('  ✅ No color accessibility issues detected!\n')
    }

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
    this.log('💡 Tip: Use --all-types to test all 7 CVD types')
    this.log('💡 Tip: Use --min-contrast 7.0 for WCAG AAA compliance\n')
  }

  private async extractColorPairs(page: Page): Promise<ColorPair[]> {
    return page.evaluate(() => {
      const pairs: Array<{
        background: string
        contrastRatio: number
        element: string
        foreground: string
        selector: string
        textContent?: string
      }> = []

      const seen = new Set<string>()

      // Get all text-containing elements
      const textElements = document.querySelectorAll(
        'p, span, h1, h2, h3, h4, h5, h6, a, button, label, li, td, th, div, section, article'
      )

      for (const el of textElements) {
        // Skip empty elements
        const text = el.textContent?.trim()
        if (!text || text.length === 0) continue

        // Get computed styles
        const style = globalThis.getComputedStyle(el)

        // Get colors
        const fgColor = style.color
        const bgColor = style.backgroundColor

        // Skip if no valid colors
        if (!fgColor || !bgColor) continue
        if (bgColor === 'rgba(0, 0, 0, 0)' || bgColor === 'transparent') continue

        // Create unique key to avoid duplicates
        const key = `${fgColor}-${bgColor}`
        if (seen.has(key)) continue
        seen.add(key)

        // Parse RGB values
        const fgMatch = fgColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
        const bgMatch = bgColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)

        if (!fgMatch || !bgMatch) continue

        // Convert to hex (in browser context, can't move outside)
        // eslint-disable-next-line unicorn/consistent-function-scoping
        const toHex = (r: string, g: string, b: string) =>
          '#' +
          [r, g, b]
            .map((x) =>
              Number.parseInt(x, 10)
                .toString(16)
                .padStart(2, '0')
            )
            .join('')

        const fgHex = toHex(fgMatch[1], fgMatch[2], fgMatch[3])
        const bgHex = toHex(bgMatch[1], bgMatch[2], bgMatch[3])

        // Calculate contrast ratio (in browser context, can't move outside)
        // eslint-disable-next-line unicorn/consistent-function-scoping
        const getLuminance = (hex: string) => {
          const rgb = [
            Number.parseInt(hex.slice(1, 3), 16) / 255,
            Number.parseInt(hex.slice(3, 5), 16) / 255,
            Number.parseInt(hex.slice(5, 7), 16) / 255,
          ].map((c) => (c <= 0.039_28 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))

          return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        }

        const l1 = getLuminance(fgHex)
        const l2 = getLuminance(bgHex)
        const contrastRatio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)

        // Get selector
        let selector = ''
        if (el.id) {
          selector = `#${el.id}`
        } else if (el.className && typeof el.className === 'string') {
          selector = `.${el.className.split(' ')[0]}`
        } else {
          selector = el.tagName.toLowerCase()
        }

        pairs.push({
          background: bgHex,
          contrastRatio,
          element: el.tagName.toLowerCase(),
          foreground: fgHex,
          selector,
          textContent: text.slice(0, 50),
        })

        // Limit to 100 pairs for performance
        if (pairs.length >= 100) break
      }

      return pairs
    })
  }

  private getScoreDisplay(score: number): string {
    if (score >= 80) {
      return pc.green(`${score}%`)
    }

    if (score >= 50) {
      return pc.yellow(`${score}%`)
    }

    return pc.red(`${score}%`)
  }

  private getSeverityIcon(severity: string): string {
    switch (severity) {
      case 'critical': {
        return '🔴'
      }

      case 'moderate': {
        return '🟡'
      }

      case 'serious': {
        return '🟠'
      }

      default: {
        return '⚪'
      }
    }
  }

  private hexToRgb(hex: string): [number, number, number] {
    const cleaned = hex.replace('#', '')
    return [
      Number.parseInt(cleaned.slice(0, 2), 16),
      Number.parseInt(cleaned.slice(2, 4), 16),
      Number.parseInt(cleaned.slice(4, 6), 16),
    ]
  }

  private rgbToHex(rgb: [number, number, number]): string {
    return (
      '#' +
      rgb
        .map((c) =>
          Math.max(0, Math.min(255, Math.round(c)))
            .toString(16)
            .padStart(2, '0')
        )
        .join('')
    )
  }

  private simulateCVD(hexColor: string, cvdType: CVDType): string {
    const rgb = this.hexToRgb(hexColor)
    const normalized = rgb.map((c) => c / 255)

    // Get transformation matrix
    let matrix: number[][]

    switch (cvdType) {
    case 'deuteranomaly': {
      // Blend of normal (40%) and deuteranopia (60%)
      matrix = CVD_MATRICES.deuteranopia.map((row, i) => row.map((v, j) => 0.4 * (i === j ? 1 : 0) + 0.6 * v))
    
    break;
    }

    case 'protanomaly': {
      // Blend of normal (50%) and protanopia (50%)
      matrix = CVD_MATRICES.protanopia.map((row, i) => row.map((v, j) => 0.5 * (i === j ? 1 : 0) + 0.5 * v))
    
    break;
    }

    case 'tritanomaly': {
      // Blend of normal (50%) and tritanopia (50%)
      matrix = CVD_MATRICES.tritanopia.map((row, i) => row.map((v, j) => 0.5 * (i === j ? 1 : 0) + 0.5 * v))
    
    break;
    }

    default: {
      matrix = CVD_MATRICES[cvdType] || [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
      ]
    }
    }

    // Apply matrix transformation
    const simulated = matrix.map((row) => row.reduce((sum, val, i) => sum + val * normalized[i], 0))

    // Denormalize and clamp
    const result = simulated.map((c) => Math.max(0, Math.min(255, c * 255))) as [number, number, number]

    return this.rgbToHex(result)
  }

  private suggestFix(cvdType: CVDType, contrast: number): string {
    const suggestions: string[] = []

    if (contrast < 3) {
      suggestions.push('Increase contrast significantly', 'Add patterns or icons, not just color')
    } else {
      suggestions.push('Slightly increase contrast')
    }

    if (['deuteranomaly', 'deuteranopia', 'protanomaly', 'protanopia'].includes(cvdType)) {
      suggestions.push('Avoid red/green combinations - use blue/yellow instead')
    } else if (['tritanomaly', 'tritanopia'].includes(cvdType)) {
      suggestions.push('Avoid blue/yellow combinations')
    }

    return suggestions[0]
  }
}
