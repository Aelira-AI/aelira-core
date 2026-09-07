import Ajv from 'ajv'
import {expect} from 'chai'
import {readFile} from 'node:fs/promises'

import {
  type AxeViolation,
  buildSarifLog,
  calculateCiExitCode,
  resolveSarifSource,
} from '../../src/utils/sarif.js'

const IMAGE_HTML = '<img id="hero" src="hero.png">'
const BUTTON_HTML = '<button id="save"></button>'

function violation(overrides: Partial<AxeViolation> = {}): AxeViolation {
  return {
    description: 'Ensures images have alternate text',
    help: 'Images must have alternate text',
    helpUrl: 'https://dequeuniversity.com/rules/axe/4.11/image-alt',
    id: 'image-alt',
    impact: 'critical',
    nodes: [
      {
        failureSummary: 'Fix this image by adding an alt attribute.',
        html: IMAGE_HTML,
        target: ['#hero'],
      },
    ],
    tags: ['cat.text-alternatives', 'wcag2a', 'wcag111'],
    ...overrides,
  }
}

describe('SARIF projection', () => {
  it('emits stable SARIF 2.1.0 tool, rule, and result metadata', () => {
    const log = buildSarifLog({
      axeResults: {violations: [violation()]},
      target: 'fixtures/page.html',
      toolVersion: '0.9.7',
    })

    expect(log.version).to.equal('2.1.0')
    expect(log.$schema).to.equal(
      'https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json',
    )
    expect(log.runs[0].tool.driver).to.deep.include({
      informationUri: 'https://github.com/Aelira-AI/aelira-core',
      name: 'Aelira',
      semanticVersion: '0.9.7',
    })

    const rule = log.runs[0].tool.driver.rules[0]
    expect(rule).to.deep.include({
      helpUri: 'https://dequeuniversity.com/rules/axe/4.11/image-alt',
      id: 'image-alt',
      name: 'image-alt',
    })
    expect(rule.shortDescription.text).to.equal('Images must have alternate text')
    expect(rule.fullDescription.text).to.equal('Ensures images have alternate text')

    const result = log.runs[0].results[0]
    expect(result.level).to.equal('error')
    expect(result.message.text).to.equal('Fix this image by adding an alt attribute.')
    expect(result.properties).to.deep.include({
      helpUri: 'https://dequeuniversity.com/rules/axe/4.11/image-alt',
      impact: 'critical',
    })
    expect(result.partialFingerprints['aelira/v1']).to.match(/^[a-f\d]{64}$/)
  })

  it('sorts rules and results deterministically and preserves fingerprints across input order', () => {
    const buttonViolation = violation({
      description: 'Ensures buttons have names',
      help: 'Buttons must have discernible text',
      helpUrl: 'https://dequeuniversity.com/rules/axe/4.11/button-name',
      id: 'button-name',
      impact: 'serious',
      nodes: [{failureSummary: 'Add button text.', html: BUTTON_HTML, target: ['#save']}],
    })
    const first = buildSarifLog({
      axeResults: {violations: [violation(), buttonViolation]},
      target: 'fixtures/page.html',
      toolVersion: '0.9.7',
    })
    const second = buildSarifLog({
      axeResults: {violations: [buttonViolation, violation()]},
      target: 'fixtures/page.html',
      toolVersion: '0.9.7',
    })

    expect(second).to.deep.equal(first)
    expect(first.runs[0].tool.driver.rules.map((rule) => rule.id)).to.deep.equal([
      'button-name',
      'image-alt',
    ])
  })

  it('maps unique exact local evidence to a normalized bounded source region', () => {
    const sourceText = `<!doctype html>\n<html>\n  <body>\n    ${IMAGE_HTML}\n  </body>\n</html>\n`
    const log = buildSarifLog({
      axeResults: {violations: [violation()]},
      source: {text: sourceText, uri: String.raw`fixtures\page #1.html`},
      target: './fixtures/page.html',
      toolVersion: '0.9.7',
    })

    const location = log.runs[0].results[0].locations?.[0].physicalLocation
    expect(location?.artifactLocation.uri).to.equal('fixtures/page%20%231.html')
    expect(location?.region).to.deep.equal({
      endColumn: 35,
      endLine: 4,
      startColumn: 5,
      startLine: 4,
    })
  })

  it('resolves source artifacts relative to the enclosing repository root', async () => {
    const source = await resolveSarifSource('test-sample.html', process.cwd())

    expect(source?.uri).to.equal('cli/test-sample.html')
    expect(source?.text).to.contain('Test Accessibility Page')
  })

  it('omits guessed locations for ambiguous local evidence and remote targets', () => {
    const ambiguous = buildSarifLog({
      axeResults: {violations: [violation()]},
      source: {text: `${IMAGE_HTML}\n${IMAGE_HTML}`, uri: 'page.html'},
      target: './page.html',
      toolVersion: '0.9.7',
    })
    const remote = buildSarifLog({
      axeResults: {violations: [violation()]},
      target: 'https://example.com/page',
      toolVersion: '0.9.7',
    })

    expect(ambiguous.runs[0].results[0].locations).to.equal(undefined)
    expect(remote.runs[0].results[0].locations).to.equal(undefined)
    expect(remote.runs[0].properties).to.deep.include({
      sourceMapping: 'unavailable',
      target: 'https://example.com/page',
    })
  })

  it('validates complete and location-free output against the official OASIS schema', async () => {
    const schema = JSON.parse(
      await readFile(new URL('../fixtures/sarif-schema-2.1.0.json', import.meta.url), 'utf8'),
    )
    const validate = new Ajv({allErrors: true, schemaId: 'auto'}).compile(schema)
    const local = buildSarifLog({
      axeResults: {violations: [violation()]},
      source: {text: IMAGE_HTML, uri: 'page.html'},
      target: './page.html',
      toolVersion: '0.9.7',
    })
    const remote = buildSarifLog({
      axeResults: {violations: [violation()]},
      target: 'https://example.com/page',
      toolVersion: '0.9.7',
    })

    expect(validate(local), JSON.stringify(validate.errors)).to.equal(true)
    expect(validate(remote), JSON.stringify(validate.errors)).to.equal(true)
  })

  it('keeps threshold and fail-on decisions independent of output format', () => {
    const violations = [violation({impact: 'serious'})]
    expect(calculateCiExitCode({score: 90, violations}, 80, 'critical')).to.equal(0)
    expect(calculateCiExitCode({score: 90, violations}, 80, 'serious')).to.equal(1)
    expect(calculateCiExitCode({score: 79, violations: []}, 80, 'critical')).to.equal(1)
  })
})
