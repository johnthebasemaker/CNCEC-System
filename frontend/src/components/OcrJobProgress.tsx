/**
 * OcrJobProgress — what a long vision read looks like while it is working.
 *
 * ⚠️ THIS EXISTS BECAUSE THE OLD COPY WAS A LIE, AND THE LIE WAS THE BUG.
 * Both upload cards said "This usually takes under a minute" and then showed an
 * unlabelled spinner. Measured on the operator's own documents (2026-09-02,
 * dev Mac, qwen2.5vl:7b) the same code took 92 s for a delivery note, 212 s for
 * a 30-row handwritten sheet and 399 s for a five-row printed form — and all
 * three read CORRECTLY. The reported fault, "it only loads and never gets the
 * results", was a six-and-a-half-minute job behind a sixty-second promise: the
 * person watching gave up, which is the right response to a progress indicator
 * that has stopped meaning anything.
 *
 * So this component's job is not decoration. It answers the three questions a
 * spinner cannot:
 *
 *   how long has it been?      → a live counter, from the SERVER's clock
 *   how long should it take?   → the measured median for THIS lane
 *   is anything still alive?   → `stale`, the same predicate the orphan sweep
 *                                uses, so the banner and the sweep can never
 *                                disagree about what a dead job is
 *
 * ⚠️ ELAPSED IS SERVER-DERIVED, NOT `Date.now() - uploadedAt`. A phone with a
 * wrong clock, a page reloaded mid-read, or a job adopted from another tab all
 * produce a client-side number that is confidently wrong. We take the server's
 * `elapsed_s` at each poll and only tick between polls.
 */
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Progress, Space, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

export type OcrJobStatus = {
  status?: string
  error?: string | null
  elapsed_s?: number
  expected_s?: number
  stale?: boolean
  stale_after_s?: number
  can_requeue?: boolean
}

function mmss(total: number): string {
  const s = Math.max(0, Math.floor(total))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Seconds since the last server reading, ticking once a second between polls. */
function useLiveElapsed(serverElapsed: number | undefined, active: boolean): number {
  const [tick, setTick] = useState(0)
  const anchor = useRef({ server: 0, at: Date.now() })

  useEffect(() => {
    if (serverElapsed == null) return
    anchor.current = { server: serverElapsed, at: Date.now() }
    setTick((t) => t + 1)
  }, [serverElapsed])

  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setTick((t) => t + 1), 1000)
    return () => window.clearInterval(id)
  }, [active])

  void tick
  if (serverElapsed == null) return 0
  return anchor.current.server + (Date.now() - anchor.current.at) / 1000
}

export default function OcrJobProgress({
  job, what, onRetry, retrying = false,
}: {
  job: OcrJobStatus | undefined
  /** What is being read, for the sentence: "Reading the delivery note…" */
  what: string
  /** Re-run the read. Given `stale`, the server has already concluded nobody is working on it. */
  onRetry?: () => void
  retrying?: boolean
}) {
  const status = job?.status ?? 'queued'
  const active = status === 'queued' || status === 'running'
  const elapsed = useLiveElapsed(job?.elapsed_s, active)
  const expected = job?.expected_s ?? 180

  // ⚠️ INTERRUPTED IS THE SERVER'S VERDICT, NOT A TIMER IN THE BROWSER. A read
  // that legitimately runs for fifteen minutes keeps beating and must never be
  // shown as broken; a worker that died two minutes in must never be shown as
  // working. Only the heartbeat can tell those apart, and only the server sees it.
  if (active && job?.stale) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginTop: 10 }}
        message="This read was interrupted"
        description={
          <>
            <Typography.Paragraph style={{ marginBottom: 8 }}>
              The server process that was reading your page stopped responding
              about {mmss(job.stale_after_s ?? 180)} ago. Nothing is working on
              it now, so waiting longer will not help.
            </Typography.Paragraph>
            {onRetry && (
              <Button size="small" icon={<ReloadOutlined />} loading={retrying}
                onClick={onRetry}>
                {job.can_requeue ? 'Read it again' : 'Upload the photo again'}
              </Button>
            )}
          </>
        }
      />
    )
  }

  if (!active) return null

  // Past the expected time the bar stops advancing rather than filling up: a
  // progress bar that reaches 100 % and keeps spinning is a second broken
  // promise, and this whole component exists because of the first one.
  const pct = Math.min(99, Math.round((elapsed / expected) * 100))
  const over = elapsed > expected * 1.25

  return (
    <div style={{ marginTop: 10 }}>
      <Progress percent={pct} status="active" showInfo={false} size="small" />
      <Space size={8} wrap style={{ marginTop: 6 }}>
        <Typography.Text strong>{mmss(elapsed)}</Typography.Text>
        <Typography.Text type="secondary">
          {status === 'queued'
            ? `Queued — the server reads one page at a time. ${what} usually takes about ${mmss(expected)} once it starts.`
            : over
              ? `Still reading ${what}. This one is running longer than the usual ${mmss(expected)} — a dense or a badly-lit page takes more. It is still working; the page will update itself.`
              : `Reading ${what} — these usually take about ${mmss(expected)} on this server.`}
        </Typography.Text>
      </Space>
      <Typography.Paragraph type="secondary"
        style={{ marginBottom: 0, marginTop: 4, fontSize: 12 }}>
        You can leave this page — the read carries on and the result will be
        waiting.
      </Typography.Paragraph>
    </div>
  )
}
