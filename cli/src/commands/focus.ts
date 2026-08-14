import type { Browser, Page } from 'playwright'

import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { chromium } from 'playwright'

interface FocusableElement {
  ariaLabel?: string
  boundingBox?: { height: number; width: number; x: number; y: number }
  elementId: number
  isOffscreen: boolean
  isVisible: boolean
  role?: string
  selector: string
  tabIndex?: number
  tagName: string
  textContent?: string
}

interface FocusOrderIssue {
  description: string
  element?: FocusableElement
  issueType: string
  severity: string
  suggestedFix?: string
  wcagCriterion: string
}

interface FocusOrderResult {
  complianceScore: number
  focusSequence: FocusableElement[]
  issues: FocusOrderIssue[]
  totalFocusableElements: number
  url: string
  wcagCompliant: boolean
}

export default class Focus extends Command {
  static args = {
    url: Args.string({
      description: 'URL or HTML file to analyze for keyboard focus order',
      required: true,
    }),
  }
static description =
    'Analyze keyboard focus order for WCAG 2.4.3 compliance - detects focus traps, invisible elements, and illogical tab order'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com',
    '<%= config.bin %> <%= command.id %> https://example.com --max-tabs 150',
    '<%= config.bin %> <%= command.id %> ./index.html --format json --output focus-report.json',
  ]
static flags = {
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    'max-tabs': Flags.integer({
      default: 100,
      description: 'Maximum number of TAB keys to simulate',
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
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Focus)
    const startTime = Date.now()

    intro('Aelira CLI - Focus Order Analyzer (WCAG 2.4.3)')

    let browser: Browser | null = null
    let page: null | Page = null

    try {
      const s = spinner()
      s.start('Launching browser...')

      browser = await chromium.launch({ headless: true })
      const context = await browser.newContext()
      page = await context.newPage()

      s.message('Navigating to ' + args.url + '...')

      // Handle file:// URLs for local HTML files
      let targetUrl = args.url
      if (!args.url.startsWith('http')) {
        const absolutePath = path.resolve(args.url)
        await fs.access(absolutePath)
        targetUrl = `file://${absolutePath}`
      }

      await page.goto(targetUrl, { timeout: flags.timeout })

      s.message('Analyzing keyboard focus order...')
      const analysisStart = Date.now()

      // Track focus sequence
      const focusSequence = await this.trackFocusSequence(page, flags['max-tabs'])

      // Detect issues
      const issues = await this.detectFocusIssues(page, focusSequence)

      // Calculate compliance score
      const complianceScore = this.calculateComplianceScore(focusSequence, issues)
      const wcagCompliant = complianceScore >= 80 && !issues.some((i) => i.severity === 'critical')

      const analysisDuration = Date.now() - analysisStart
      s.stop(`Analysis complete in ${analysisDuration}ms`)

      const result: FocusOrderResult = {
        complianceScore,
        focusSequence,
        issues,
        totalFocusableElements: focusSequence.length,
        url: args.url,
        wcagCompliant,
      }

      // Output results
      if (flags.format === 'json') {
        const output = {
          ...result,
          performance: {
            analysis_time: analysisDuration,
            total_time: Date.now() - startTime,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Focus order report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displayResults(result, analysisDuration)
        outro(wcagCompliant ? '✅ WCAG 2.4.3 Focus Order: Compliant' : '⚠️  WCAG 2.4.3 Focus Order: Needs Improvement')
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

  private calculateComplianceScore(focusSequence: FocusableElement[], issues: FocusOrderIssue[]): number {
    if (focusSequence.length === 0) {
      return 0
    }

    let score = 100

    const severityWeights: Record<string, number> = {
      critical: 30,
      minor: 2,
      moderate: 5,
      serious: 15,
    }

    for (const issue of issues) {
      score -= severityWeights[issue.severity] || 5
    }

    return Math.max(0, Math.min(100, score))
  }

  private async detectFocusIssues(page: Page, focusSequence: FocusableElement[]): Promise<FocusOrderIssue[]> {
    const issues: FocusOrderIssue[] = []

    // 1. Detect invisible elements in focus order
    for (const element of focusSequence) {
      if (!element.isVisible) {
        issues.push({
          description: `Element is in focus order but not visible: ${element.selector}`,
          element,
          issueType: 'invisible_element',
          severity: 'serious',
          suggestedFix:
            "Remove tabindex or set to tabindex='-1' for invisible elements, or ensure element is visible when focused.",
          wcagCriterion: '2.4.3',
        })
      }
    }

    // 2. Detect off-screen elements (check if skip links)
    for (const element of focusSequence) {
      if (element.isOffscreen) {
        const isSkipLink =
          element.textContent &&
          (element.textContent.toLowerCase().includes('skip') || element.textContent.toLowerCase().includes('jump'))

        if (!isSkipLink) {
          issues.push({
            description: `Element is positioned off-screen but in focus order: ${element.selector}`,
            element,
            issueType: 'offscreen_element',
            severity: 'moderate',
            suggestedFix:
              'If this is a skip link, ensure it becomes visible on focus. If not, remove from focus order.',
            wcagCriterion: '2.4.3',
          })
        }
      }
    }

    // 3. Detect illogical focus order (large visual jumps)
    for (let i = 1; i < focusSequence.length; i++) {
      const prev = focusSequence[i - 1]
      const current = focusSequence[i]

      if (prev.boundingBox && current.boundingBox) {
        const prevCenterX = prev.boundingBox.x + prev.boundingBox.width / 2
        const prevCenterY = prev.boundingBox.y + prev.boundingBox.height / 2
        const currentCenterX = current.boundingBox.x + current.boundingBox.width / 2
        const currentCenterY = current.boundingBox.y + current.boundingBox.height / 2

        const distance = Math.hypot(
          (currentCenterX - prevCenterX), (currentCenterY - prevCenterY)
        )

        if (distance > 500) {
          issues.push({
            description: `Large visual jump in focus order (from ${prev.selector} to ${current.selector})`,
            element: current,
            issueType: 'illogical_order',
            severity: 'moderate',
            suggestedFix: 'Reorder HTML or use tabindex to create logical focus order that matches visual layout.',
            wcagCriterion: '2.4.3',
          })
        }
      }
    }

    // 4. Detect potential focus traps
    const totalInteractive = await page.evaluate(() => {
      const selectors = 'a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
      return document.querySelectorAll(selectors).length
    })

    if (totalInteractive > 10 && focusSequence.length < 5) {
      issues.push({
        description: `Potential focus trap detected: ${totalInteractive} interactive elements but only ${focusSequence.length} reachable via keyboard`,
        issueType: 'focus_trap',
        severity: 'critical',
        suggestedFix:
          'Remove JavaScript that captures TAB key or restricts focus movement. Ensure all interactive elements are keyboard-accessible.',
        wcagCriterion: '2.1.2',
      })
    }

    // 5. Check for missing focus indicators
    const focusStyleInfo = await page.evaluate(() => {
      const focusableElements = document.querySelectorAll(
        'a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
      let withoutFocusStyle = 0

      focusableElements.forEach((el: Element) => {
        const htmlEl = el as HTMLElement
        const originalFocus = document.activeElement
        htmlEl.focus()

        const style = globalThis.getComputedStyle(el)
        const hasFocusStyle =
          (style.outline !== 'none' && style.outline !== 'rgb(0, 0, 0) none 0px' && style.outlineWidth !== '0px') ||
          style.boxShadow !== 'none'

        if (!hasFocusStyle) {
          withoutFocusStyle++
        }

        if (originalFocus instanceof HTMLElement) {
          originalFocus.focus()
        }
      })

      return { total: focusableElements.length, withoutFocusStyle }
    })

    if (focusStyleInfo.withoutFocusStyle > 0 && focusStyleInfo.total > 0) {
      const percentage = (focusStyleInfo.withoutFocusStyle / focusStyleInfo.total) * 100
      if (percentage > 50) {
        issues.push({
          description: `${focusStyleInfo.withoutFocusStyle} elements (${percentage.toFixed(1)}%) lack visible focus indicators`,
          issueType: 'missing_focus_indicator',
          severity: 'serious',
          suggestedFix:
            'Add :focus styles with visible outline or box-shadow to all interactive elements. Ensure focus is clearly visible.',
          wcagCriterion: '2.4.7',
        })
      }
    }

    return issues
  }

  private displayResults(result: FocusOrderResult, analysisTime: number): void {
    // Header
    this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  Focus Order Analysis Report')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  URL: ${result.url}`)
    this.log(`  Focusable Elements: ${result.totalFocusableElements}`)
    this.log(`  Compliance Score: ${this.getScoreDisplay(result.complianceScore)}`)
    this.log(`  WCAG 2.4.3 Status: ${result.wcagCompliant ? pc.green('Compliant') : pc.yellow('Needs Work')}`)
    this.log(`  Analysis Time: ${analysisTime}ms\n`)

    // Focus Sequence
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  ⌨️  Focus Sequence (Tab Order)')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const maxDisplay = 20
    for (let i = 0; i < Math.min(result.focusSequence.length, maxDisplay); i++) {
      const el = result.focusSequence[i]
      const visibility = el.isVisible ? '' : pc.yellow(' [hidden]')
      const offscreen = el.isOffscreen ? pc.dim(' [offscreen]') : ''
      const label = el.ariaLabel || el.textContent?.slice(0, 30) || el.role || ''

      this.log(`  ${pc.cyan(String(i + 1).padStart(3))} │ <${el.tagName}> ${label}${visibility}${offscreen}`)
    }

    if (result.focusSequence.length > maxDisplay) {
      this.log(`\n  ... and ${result.focusSequence.length - maxDisplay} more elements`)
    }

    // Issues
    if (result.issues.length > 0) {
      this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log(`  ⚠️  Issues Found (${result.issues.length})`)
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

      for (const issue of result.issues) {
        const severityIcon = this.getSeverityIcon(issue.severity)
        const severityColor = this.getSeverityColor(issue.severity)

        this.log(`  ${severityIcon} ${severityColor(`[${issue.severity.toUpperCase()}]`)} ${issue.description}`)
        this.log(`     WCAG: ${issue.wcagCriterion}`)
        if (issue.suggestedFix) {
          this.log(`     💡 Fix: ${issue.suggestedFix}`)
        }

        this.log('')
      }
    } else {
      this.log('\n  ✅ No focus order issues detected!\n')
    }

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
    this.log('💡 Tip: Use --format json for detailed element data')
    this.log('💡 Tip: Use --max-tabs 200 for pages with many elements\n')
  }

  private getScoreDisplay(score: number): string {
    if (score >= 80) {
      return pc.green(`${score.toFixed(0)}%`)
    }

    if (score >= 50) {
      return pc.yellow(`${score.toFixed(0)}%`)
    }

    return pc.red(`${score.toFixed(0)}%`)
  }

  private getSeverityColor(severity: string): (text: string) => string {
    switch (severity) {
      case 'critical': {
        return pc.red
      }

      case 'moderate': {
        return pc.yellow
      }

      case 'serious': {
        return pc.magenta
      }

      default: {
        return pc.dim
      }
    }
  }

  private getSeverityIcon(severity: string): string {
    switch (severity) {
      case 'critical': {
        return '🔴'
      }

      case 'minor': {
        return '⚪'
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

  private async trackFocusSequence(page: Page, maxTabs: number): Promise<FocusableElement[]> {
    const focusSequence: FocusableElement[] = []
    const seenSelectors = new Set<string>()
    let previousSelector: null | string = null

    // Focus the body first
    await page.evaluate(() => document.body.focus())

    for (let i = 0; i < maxTabs; i++) {
      // Press TAB
      await page.keyboard.press('Tab')
      await page.waitForTimeout(50) // Wait for focus to settle

      // Get currently focused element
      const elementInfo = await page.evaluate(() => {
        const el = document.activeElement
        if (!el || el === document.body) return null

        // Get unique selector
        let selector = ''
        if (el.id) {
          selector = `#${el.id}`
        } else {
          const siblings = el.parentElement
            ? [...el.parentElement.children].filter((c) => c.tagName === el.tagName)
            : []
          const index = siblings.indexOf(el) + 1
          selector = `${el.tagName.toLowerCase()}:nth-of-type(${index})`
        }

        // Get bounding box
        const rect = el.getBoundingClientRect()

        // Check if element is visible
        const style = globalThis.getComputedStyle(el)
        const isVisible =
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          style.opacity !== '0' &&
          rect.width > 0 &&
          rect.height > 0

        // Check if element is off-screen
        const isOffscreen =
          (rect.right < 0 || rect.bottom < 0 || rect.left > window.innerWidth || rect.top > window.innerHeight) &&
          isVisible

        return {
          ariaLabel: el.getAttribute('aria-label'),
          boundingBox: { height: rect.height, width: rect.width, x: rect.x, y: rect.y },
          isOffscreen,
          isVisible,
          role: el.getAttribute('role'),
          selector,
          tabIndex: (el as HTMLElement).tabIndex,
          tagName: el.tagName.toLowerCase(),
          textContent: el.textContent?.trim().slice(0, 100),
        }
      })

      // Stop if we've looped back or hit body
      if (!elementInfo) {
        break
      }

      const currentSelector = elementInfo.selector

      // Detect if we've completed one full loop
      if (seenSelectors.has(currentSelector) && focusSequence.length > 0 && currentSelector === focusSequence[0].selector) {
          break
        }

      // Skip consecutive duplicates
      if (currentSelector === previousSelector) {
        continue
      }

      seenSelectors.add(currentSelector)
      previousSelector = currentSelector

      focusSequence.push({
        ariaLabel: elementInfo.ariaLabel || undefined,
        boundingBox: elementInfo.boundingBox,
        elementId: focusSequence.length,
        isOffscreen: elementInfo.isOffscreen,
        isVisible: elementInfo.isVisible,
        role: elementInfo.role || undefined,
        selector: elementInfo.selector,
        tabIndex: elementInfo.tabIndex,
        tagName: elementInfo.tagName,
        textContent: elementInfo.textContent || undefined,
      })
    }

    return focusSequence
  }
}
