import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { pollForCompletion } from '../../utils/poll-progress.js'

interface QueuedTranscription {
  [key: string]: unknown
  scan_id?: string
}

interface TranscriptionDetails {
  full_text?: string
  segments?: unknown[]
  segments_count?: number
}

type CompletionPoller = typeof pollForCompletion

export function normalizeQueuedTranscription(completed: any, scanId: string): any {
  const scan = completed.scan ?? completed
  const output = scan.result?.structure?.transcription_output ?? {}
  const transcriptionDetails = output.transcription
  const transcription = typeof transcriptionDetails === 'string'
    ? transcriptionDetails
    : (transcriptionDetails as null | TranscriptionDetails)?.full_text ?? ''

  return {
    ...output,
    filename: output.file_name ?? scan.file_name,
    transcription,
    transcription_details:
      typeof transcriptionDetails === 'object' ? transcriptionDetails : null,
    scan_id: scanId,
    report_url: `/education/scans/${scanId}`,
    caption_formats: output.captions ?? null,
  }
}

export async function resolveQueuedTranscription(
  api: ApiClient,
  queued: QueuedTranscription,
  progress: Parameters<CompletionPoller>[2],
  poller: CompletionPoller = pollForCompletion,
): Promise<any> {
  if (!queued.scan_id) return queued
  const completed = await poller(api, queued.scan_id, progress, { timeout: 300_000 })
  return normalizeQueuedTranscription(completed, queued.scan_id)
}

export default class ScanVideo extends Command {
  static args = {
    file: Args.string({
      description: 'Video/audio file or directory to transcribe',
      required: true,
    }),
  }
static description = 'Transcribe video/audio files to accessible WebVTT and SRT captions using Whisper AI'
static examples = [
    '<%= config.bin %> <%= command.id %> lecture.mp4',
    '<%= config.bin %> <%= command.id %> ./videos/',
    '<%= config.bin %> <%= command.id %> recording.mp3 --format json',
    '<%= config.bin %> <%= command.id %> ./media/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
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
    const { args, flags } = await this.parse(ScanVideo)
    const startTime = Date.now()

    intro('Aelira CLI - Video/Audio Transcription')

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
    this.log(`  Batch Transcription Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error).length
    const failed = results.filter((r) => r.error).length

    const totalDuration = results
      .filter((r) => !r.error)
      .reduce((sum, r) => sum + (r.duration || 0), 0)

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Total Media Duration: ${(totalDuration / 60).toFixed(1)} minutes`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show top transcriptions
    const successfulTranscriptions = results.filter((r) => !r.error && r.transcription)

    if (successfulTranscriptions.length > 0) {
      this.log('Top Results:\n')
      for (const [index, result] of successfulTranscriptions.slice(0, 5).entries()) {
        this.log(`${index + 1}. ${result.file}`)
        this.log(`   Duration: ${result.duration ? result.duration.toFixed(1) + 's' : 'N/A'}`)
        this.log(`   Words: ${result.word_count || 'N/A'}`)
        this.log(`   Language: ${result.language || 'N/A'}`)
        this.log('')
      }
    }

    if (failed > 0) {
      this.log('Failed Transcriptions:\n')
      for (const result of results
        .filter((r) => r.error)) {
          this.log(`  ✗ ${result.file}: ${result.error}`)
        }

      this.log('')
    }

    this.log('💡 Tip: Use --format json --output report.json for full results')
  }

  private displaySingleResult(result: any, processTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Video/Audio Transcription Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  Duration: ${result.duration ? result.duration.toFixed(1) + 's' : 'N/A'}`)
    this.log(`  Model: ${result.model_used || 'whisper'}`)
    this.log(`  Language: ${result.language || 'N/A'}\n`)

    if (result.transcription) {
      this.log(`  Transcription Preview:`)
      const preview = result.transcription.slice(0, 200)
      this.log(`  "${preview}${result.transcription.length > 200 ? '...' : ''}"\n`)
    }

    if (result.caption_formats) {
      this.log(`  Generated Captions:`)
      if (result.caption_formats.webvtt) {
        this.log(`  ✓ WebVTT format`)
      }

      if (result.caption_formats.srt) {
        this.log(`  ✓ SRT format`)
      }

      this.log('')
    }

    if (result.word_count) {
      this.log(`  Word Count: ${result.word_count}`)
    }

    if (result.confidence) {
      this.log(`  Confidence: ${result.confidence.toFixed(1)}%`)
    }

    this.log(`\n  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (result.scan_id) {
      this.log(`  Scan ID: ${result.scan_id}`)
      this.log(`  View full report: ${result.report_url || 'N/A'}\n`)
    }

    this.log('💡 Tip: Use --format json for complete transcription and caption files')
    this.log('💡 Tip: Use directory path to batch transcribe multiple files')
  }

  private async findMediaFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []
    const mediaExtensions = new Set(['.avi', '.m4a', '.mkv', '.mov', '.mp3', '.mp4', '.ogg', '.wav', '.webm'])

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Recursively scan subdirectories
        files.push(...(await this.findMediaFiles(fullPath)))
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).toLowerCase()
        if (mediaExtensions.has(ext)) {
          files.push(fullPath)
        }
      }
    }

    return files
  }

  private async processDirectory(
    dirPath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Finding video/audio files...')

    const files = await this.findMediaFiles(dirPath)

    if (files.length === 0) {
      s.stop('No video/audio files found')
      outro('⚠️  No media files to transcribe')
      return
    }

    s.stop(`Found ${files.length} media file${files.length > 1 ? 's' : ''}`)

    const results: any[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      s.start(`Transcribing ${path.basename(file)} (${i + 1}/${files.length})...`)

      try {
        const result = await this.transcribeMedia(file, flags['api-url'], s)
        results.push({ file: path.basename(file), ...result })
        s.stop(`✓ ${path.basename(file)}`)
      } catch (error: any) {
        s.stop(`✗ ${path.basename(file)}: ${error.message}`)
        results.push({ error: error.message, file: path.basename(file) })
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
        outro(`✅ Batch transcription complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResults(results, Date.now() - startTime)
      outro(`✨ Batch transcription complete! Processed ${files.length} files`)
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
    s.start('Transcribing media file...')

    try {
      const result = await this.transcribeMedia(filePath, flags['api-url'], s)
      const processDuration = Date.now() - startTime

      s.stop('Transcription complete')

      if (flags.format === 'json') {
        const output = {
          ...result,
          performance: {
            processing_time: processDuration,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Transcription complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, processDuration)
        outro('✨ Transcription complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${processDuration}ms`)
      }
    } catch (error: any) {
      s.stop('Transcription failed')
      throw error
    }
  }

  private async transcribeMedia(filePath: string, apiUrl: string, progress: any): Promise<any> {
    const api = new ApiClient({ apiUrl })
    const formData = new FormData()
    formData.append('file', await fs.readFile(filePath), path.basename(filePath))

    const response = await api.postForm('/education/multimedia/transcribe', formData as any, {
      timeout: 300_000, // 5 minute timeout for video transcription
    })

    const queued = await response.json() as QueuedTranscription
    return resolveQueuedTranscription(api, queued, progress)
  }
}
