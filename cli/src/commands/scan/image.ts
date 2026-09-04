import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'

export default class ScanImage extends Command {
  static args = {
    file: Args.string({
      description: 'Image file or directory for AI alt text generation',
      required: true,
    }),
  }
static description = 'Generate AI-powered alt text for images using vision models'
static examples = [
    '<%= config.bin %> <%= command.id %> photo.jpg',
    '<%= config.bin %> <%= command.id %> ./images/',
    '<%= config.bin %> <%= command.id %> diagram.png --format json',
    '<%= config.bin %> <%= command.id %> ./course-images/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    batch: Flags.boolean({
      default: false,
      description: 'Use batch processing API (faster for multiple images)',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON format)',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanImage)
    const startTime = Date.now()

    intro('Aelira CLI - AI Image Alt Text Generator')

    try {
      const targetPath = path.resolve(args.file)
      const stats = await fs.stat(targetPath)

      if (stats.isDirectory()) {
        // Batch process directory
        await this.processDirectory(targetPath, flags, startTime)
      } else {
        // Single file processing
        await this.processFile(targetPath, flags, startTime)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayBatchResults(results: any[], totalTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Batch Alt Text Generation Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error && r.alt_text).length
    const failed = results.filter((r) => r.error || !r.alt_text).length

    this.log(`  Total Images: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show top results
    const successfulResults = results.filter((r) => !r.error && r.alt_text)

    if (successfulResults.length > 0) {
      this.log('Sample Results:\n')
      for (const [index, result] of successfulResults.slice(0, 3).entries()) {
        this.log(`${index + 1}. ${result.file || result.filename}`)
        this.log(`   Alt Text: "${result.alt_text}"`)
        if (result.confidence) {
          this.log(`   Confidence: ${result.confidence.toFixed(1)}%`)
        }

        this.log('')
      }
    }

    if (failed > 0) {
      this.log('Failed:\n')
      for (const result of results
        .filter((r) => r.error || !r.alt_text)) {
          this.log(`  ✗ ${result.file || result.filename}: ${result.error || 'No alt text generated'}`)
        }

      this.log('')
    }

    this.log('💡 Tip: Use --format json --output results.json for full output')
  }

  private displaySingleResult(result: any, processTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  AI Alt Text Generation Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  Model: ${result.model_used || 'llava:7b'}`)
    this.log(`  Confidence: ${result.confidence ? result.confidence.toFixed(1) + '%' : 'N/A'}\n`)

    if (result.alt_text) {
      this.log(`  Generated Alt Text:`)
      this.log(`  "${result.alt_text}"\n`)
    }

    if (result.description) {
      this.log(`  Detailed Description:`)
      this.log(`  ${result.description}\n`)
    }

    this.log(`  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('💡 Tip: Use --format json for structured output')
    this.log('💡 Tip: Use --batch flag for faster processing of multiple images')
  }

  private async findImageFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []
    const imageExts = new Set(['.bmp', '.gif', '.jpeg', '.jpg', '.png', '.svg', '.webp'])

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Recursively scan subdirectories
        files.push(...(await this.findImageFiles(fullPath)))
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).toLowerCase()
        if (imageExts.has(ext)) {
          files.push(fullPath)
        }
      }
    }

    return files
  }

  private async generateAltText(filePath: string, apiUrl: string | undefined): Promise<any> {
    const api = new ApiClient({ apiUrl })
    const formData = new FormData()
    formData.append('file', await fs.readFile(filePath), path.basename(filePath))

    const response = await api.postForm('/education/image/alt-text', formData as any, {
      timeout: 120_000, // 2 minute timeout for AI vision processing
    })

    return response.json()
  }

  private async processBatch(files: string[], apiUrl: string | undefined): Promise<any[]> {
    const api = new ApiClient({ apiUrl })
    const formData = new FormData()

    for (const file of files) {
      formData.append('files', await fs.readFile(file), path.basename(file))
    }

    const response = await api.postForm('/education/image/batch-alt-text', formData as any, {
      timeout: 300_000, // 5 minute timeout for batch processing
    })

    const result = await response.json()
    return result.results || []
  }

  private async processDirectory(
    dirPath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Finding image files...')

    const files = await this.findImageFiles(dirPath)

    if (files.length === 0) {
      s.stop('No image files found')
      outro('⚠️  No images to process')
      return
    }

    s.stop(`Found ${files.length} image${files.length > 1 ? 's' : ''}`)

    const results: any[] = []

    if (flags.batch && files.length > 1) {
      // Use batch API
      s.start(`Processing ${files.length} images in batch...`)
      try {
        const batchResult = await this.processBatch(files, flags['api-url'])
        results.push(...batchResult)
        s.stop(`✓ Batch processing complete`)
      } catch (error: any) {
        s.stop(`✗ Batch processing failed: ${error.message}`)
      }
    } else {
      // Process one by one
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        s.start(`Processing ${path.basename(file)} (${i + 1}/${files.length})...`)

        try {
          const result = await this.generateAltText(file, flags['api-url'])
          results.push({ file: path.basename(file), ...result })
          s.stop(`✓ ${path.basename(file)}`)
        } catch (error: any) {
          s.stop(`✗ ${path.basename(file)}: ${error.message}`)
          results.push({ error: error.message, file: path.basename(file) })
        }
      }
    }

    // Output results
    if (flags.format === 'json') {
      const output = {
        performance: {
          files_processed: files.length,
          total_time: Date.now() - startTime,
        },
        results,
      }

      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Batch processing complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResults(results, Date.now() - startTime)
      outro(`✨ Batch processing complete! Generated alt text for ${files.length} images`)
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
    }
  }

  private async processFile(
    filePath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Generating AI alt text...')

    try {
      const result = await this.generateAltText(filePath, flags['api-url'])
      const processDuration = Date.now() - startTime

      s.stop('Generation complete')

      if (flags.format === 'json') {
        const output = {
          ...result,
          performance: {
            processing_time: processDuration,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Alt text generation complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, processDuration)
        outro('✨ Alt text generation complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${processDuration}ms`)
      }
    } catch (error: any) {
      s.stop('Generation failed')
      throw error
    }
  }
}
