import { expect } from 'chai'

import ScanVideo, {
  normalizeQueuedTranscription,
  resolveQueuedTranscription,
} from '../../../src/commands/scan/video.js'
import { ApiConnectionError } from '../../../src/utils/api-client.js'

const progress = {
  message() {},
  stop() {},
}

describe('scan video queued transcription journey', () => {
  it('normalizes the terminal transcription for human display and preserves details for JSON', async () => {
    const result = await resolveQueuedTranscription(
      {} as any,
      { scan_id: 'scan-1' },
      progress,
      async (_api, scanId) => ({
        scan: {
          file_name: 'lecture.mp4',
          result: {
            structure: {
              transcription_output: {
                file_name: 'lecture.mp4',
                duration: 12.5,
                language: 'en',
                transcription: {
                  segments_count: 2,
                  segments: [{ text: 'Hello' }, { text: 'world' }],
                  full_text: 'Hello world',
                },
                captions: {
                  webvtt: 'WEBVTT\n\n00:00.000 --> 00:01.000\nHello',
                  srt: '1\n00:00:00,000 --> 00:00:01,000\nHello',
                },
              },
            },
          },
        },
        scan_id: scanId,
      }) as any,
    )

    expect(result.transcription).to.equal('Hello world')
    expect(result.transcription_details).to.deep.equal({
      segments_count: 2,
      segments: [{ text: 'Hello' }, { text: 'world' }],
      full_text: 'Hello world',
    })
    expect(result.caption_formats.webvtt).to.match(/^WEBVTT/)
    expect(result.caption_formats.srt).to.match(/^1/)
    expect(result.scan_id).to.equal('scan-1')
    expect(result.report_url).to.equal('/education/scans/scan-1')
  })

  it('also accepts an unwrapped scan result without changing the text contract', () => {
    const result = normalizeQueuedTranscription({
      file_name: 'audio.mp3',
      result: {
        structure: {
          transcription_output: { transcription: 'Already normalized' },
        },
      },
    }, 'scan-2')

    expect(result.transcription).to.equal('Already normalized')
    expect(result.transcription_details).to.equal(null)
  })

  it('logs the normalized transcription string and WebVTT caption in human output', () => {
    const lines: string[] = []
    const command = Object.create(ScanVideo.prototype) as ScanVideo
    command.log = (line = '') => { lines.push(line) }

    ;(command as any).displaySingleResult({
      filename: 'lecture.mp4',
      duration: 12.5,
      transcription: 'Hello from the terminal journey',
      caption_formats: {
        webvtt: 'WEBVTT\n\n00:00.000 --> 00:01.000\nHello',
        srt: '1\n00:00:00,000 --> 00:00:01,000\nHello',
      },
      scan_id: 'scan-1',
    }, 1000)

    const output = lines.join('\n')
    expect(output).to.include('"Hello from the terminal journey"')
    expect(output).to.include('✓ WebVTT format')
    expect(output).to.include('✓ SRT format')
  })

  it('surfaces a terminal worker failure without producing partial output', async () => {
    const failure = new Error('Transcription model failed')
    failure.name = 'ScanFailedError'

    try {
      await resolveQueuedTranscription(
        {} as any,
        { scan_id: 'scan-failed' },
        progress,
        async () => { throw failure },
      )
      expect.fail('expected the worker failure to reject')
    } catch (error: any) {
      expect(error).to.equal(failure)
    }
  })

  it('surfaces polling timeout and cancellation errors unchanged', async () => {
    const timeout = new ApiConnectionError('Scan timed out', 0)
    const cancellation = new Error('Scan was cancelled or could not be found')

    for (const expected of [timeout, cancellation]) {
      try {
        await resolveQueuedTranscription(
          {} as any,
          { scan_id: 'scan-terminal' },
          progress,
          async () => { throw expected },
        )
        expect.fail('expected terminal polling error to reject')
      } catch (error: any) {
        expect(error).to.equal(expected)
      }
    }
  })
})
