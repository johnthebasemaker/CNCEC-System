import { useRef, useState } from 'react'
import { App, Button, Modal, Space, Typography } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * The SOFT gate (Phase 10 Track 5, operator ruling Q5.1).
 *
 * ⚠️ IT NEVER BLOCKS WORK, AND THAT IS THE RULING, NOT A COMPROMISE. Phase 9
 * made photographing a paper form the PRIMARY way consumption is filed. A hard
 * gate would mean a supervisor standing in a plant at 06:00, holding a filled
 * sheet, cannot file it because they have not watched a six-minute video. This
 * project has twice ruled against that shape — FEFO is allow-and-log, and the
 * MTC gate was moved out of receipt precisely because "refusing to record
 * something that has physically happened is the one thing an inventory system
 * must never do".
 *
 * ⚠️ IT GATES THE ACTION, NOT THE PAGE — and the first version got this wrong
 * in a way worth recording. Rendering the modal on mount blocked the ENTIRE
 * ExecutionPage, including the "Print a consumption form" card, so a supervisor
 * who wanted a BLANK sheet was stopped by a video about filling one in. The
 * Playwright suite caught it as a modal intercepting pointer events on an
 * unrelated control; the real defect was that the gate had the wrong scope.
 *
 * So `children` is a render prop receiving `guard`. Wrap only the action that
 * is actually gated:
 *
 *     <TrainingGate feature="ocr_upload">
 *       {(guard) => <Upload beforeUpload={(f) => { guard(() => upload(f)); return false }} />}
 *     </TrainingGate>
 *
 * Untrained user clicks upload → unskippable interstitial → "Watch later"
 * records the deferral AND THEN RUNS THE ACTION, so the click is never wasted
 * and nothing is refused.
 *
 * ⚠️ AND THE SERVER AGREES. `GET /training/gate/{feature}` returns
 * `allowed: true` unconditionally; only `show_interstitial` varies. If a future
 * slice makes this hard, the refusal must move SERVER-SIDE into
 * `POST /execution/ocr/upload` — a UI-only gate is not a control.
 */
export default function TrainingGate(
  { feature, children }:
  { feature: string; children: (guard: (run: () => void) => void) => React.ReactNode },
) {
  const { message } = App.useApp()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  // Cleared once the person has answered the interstitial in this session, so
  // a second upload does not ask again.
  const [answered, setAnswered] = useState(false)
  const pendingAction = useRef<(() => void) | null>(null)

  const { data } = useQuery({
    queryKey: ['/training/gate', feature],
    queryFn: async () => (await api.get(`/training/gate/${feature}`)).data,
    // The answer changes only when somebody acknowledges a module, which is a
    // deliberate act on another page. Re-asking on every focus would be a
    // request per tab switch for a value that almost never moves.
    staleTime: 60_000,
  })
  const pending: { module_key: string; title: string }[] = data?.pending ?? []

  const guard = (run: () => void) => {
    if (answered || !data?.show_interstitial) { run(); return }
    pendingAction.current = run
    setOpen(true)
  }

  const later = async () => {
    setBusy(true)
    try {
      // Recorded before the action runs, so a deferral cannot be lost to a
      // navigation. This is the only thing the soft gate actually enforces.
      await Promise.all(pending.map((p) =>
        api.post('/training/defer', { module_key: p.module_key })))
    } catch {
      // A failed write must not trap somebody behind a modal that will not
      // close — the whole design is that this never blocks work.
      message.warning('Could not record that, but carry on.')
    } finally {
      setBusy(false)
      setAnswered(true)
      setOpen(false)
      const run = pendingAction.current
      pendingAction.current = null
      run?.()          // the click is honoured, not thrown away
    }
  }

  return (
    <>
      <Modal
        open={open}
        title={<Space><PlayCircleOutlined />Training you have not completed</Space>}
        closable={false}
        maskClosable={false}
        keyboard={false}
        footer={[
          <Button key="later" loading={busy} onClick={later}>
            Watch later &amp; continue
          </Button>,
          <Button
            key="watch" type="primary"
            onClick={() => {
              pendingAction.current = null
              setOpen(false)
              nav('/training')
            }}
          >
            Watch now
          </Button>,
        ]}
      >
        <Typography.Paragraph>
          You have not yet completed{' '}
          <b>{pending.map((p) => p.title).join(', ') || 'the required training'}</b>.
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          Nothing is blocked — “Watch later &amp; continue” carries straight on
          with what you were doing. It is recorded, and shows on your HOD’s
          compliance list.
        </Typography.Paragraph>
      </Modal>
      {children(guard)}
    </>
  )
}
