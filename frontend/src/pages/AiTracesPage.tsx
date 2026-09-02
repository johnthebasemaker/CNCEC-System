/**
 * AI Traces — what the assistant was actually shown, and how long each stage took.
 *
 * ⚠️ THE COLUMN THAT JUSTIFIES THIS PAGE IS "RETRIEVAL", NOT "LATENCY.
 *
 * `manual_index.Index` has always computed a BM25 score for every candidate
 * chunk and then discarded all of them, so a good answer and a bad answer left
 * identical evidence: none. "The assistant said something wrong" could not be
 * split into "it retrieved the wrong passage" and "it ignored the right one",
 * and those have completely different fixes. The 800-character truncation that
 * kept §2's access matrix out of every non-admin prompt — so the assistant
 * inferred HODs could not open the Manpower page — was a retrieval failure
 * wearing a model failure's clothes, and it survived a whole phase.
 *
 * ⚠️ CHAPTER NUMBERS AND SCORES, NEVER CHAPTER TEXT. The backend records
 * `{chapter, heading, score, rank, chars}` and no passages. Rendering the text
 * here would put manual content behind a laxer read path than the manual has
 * itself, which is how rule 9's fence gets undone by a feature nobody thought
 * was about security. Everything diagnostic is answerable without it.
 *
 * Mounted twice on purpose: as a tab inside the Admin Console (`minLevel: 4`)
 * and as its own route for the Auditor, who is level 3 and can therefore never
 * open the Console. One component, two mounts — the alternative was widening
 * the whole Console to a role that may not use any of its other controls.
 */
import { useState } from 'react'
import { Alert, Card, Descriptions, Empty, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

type SpanRow = {
  span: string
  duration_ms: number | null
  ok: boolean
  outcome: string | null
  attrs: Record<string, unknown>
}

type TraceRow = {
  trace_id: string
  lane: string | null
  role: string | null
  username: string | null
  Site_ID: string | null
  created_at: string | null
  duration_ms: number | null
  ok: boolean
  outcome: string | null
  question: string | null
  queued_ms: number | null
  top_score?: number | null
  fallback?: boolean
  candidates?: number | null
  spans: SpanRow[]
}

type Stats = {
  enabled: boolean; queued: number; capacity: number
  written: number; dropped: number; draining: boolean
}

const OUTCOME_COLOR: Record<string, string> = {
  ok: 'green', greeting: 'blue', disabled: 'default',
  error: 'red', refused: 'orange', fallback: 'gold',
}

const ms = (v: number | null | undefined) =>
  v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`

function RetrievalCell({ row }: { row: TraceRow }) {
  const hit = row.spans.find((s) => s.span === 'ai.retrieve')
  if (!hit) return <Typography.Text type="secondary">—</Typography.Text>
  const a = hit.attrs as {
    allowed_chapters?: number[]; candidates?: number; top_score?: number
    fallback?: boolean; context_chars?: number
    hits?: { chapter: number; heading: string; score: number; rank: number; chars: number; used: boolean }[]
  }
  if (a.fallback) {
    // ⚠️ THE QUIET FAILURE. The answer still arrives — from a head-truncated
    // dump of every allowed chapter rather than from the passage that answers
    // the question, which is exactly the condition under which a model
    // confabulates. Without this being visible, a systematic retrieval failure
    // reads as a model that has got worse.
    return (
      <Tooltip title={`Nothing scored. The prompt fell back to a truncated dump of all ${a.allowed_chapters?.length ?? 0} allowed chapters — the model was answering without the passage.`}>
        <Tag color="gold">fallback</Tag>
      </Tooltip>
    )
  }
  const used = (a.hits ?? []).filter((h) => h.used)
  return (
    <Tooltip
      title={
        <div style={{ fontSize: 12 }}>
          <div>{a.candidates ?? 0} candidate chunk(s) inside the role&apos;s fence</div>
          {used.map((h) => (
            <div key={`${h.chapter}-${h.rank}`}>
              §{h.chapter} {h.heading || '(lead-in)'} — {h.score.toFixed(2)}
            </div>
          ))}
        </div>
      }>
      <Space size={4} wrap>
        {used.slice(0, 4).map((h) => (
          <Tag key={`${h.chapter}-${h.rank}`}>§{h.chapter}</Tag>
        ))}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          top {(a.top_score ?? 0).toFixed(2)}
        </Typography.Text>
      </Space>
    </Tooltip>
  )
}

function SpanDetail({ row }: { row: TraceRow }) {
  return (
    <div style={{ padding: '4px 0 8px' }}>
      {row.question && (
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary">Question: </Typography.Text>
          <Typography.Text code>{row.question}</Typography.Text>
        </Typography.Paragraph>
      )}
      {row.spans.length === 0 ? (
        <Empty description="No child spans — this request ended before retrieval"
          image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Descriptions size="small" bordered column={1}>
          {row.spans.map((s) => (
            <Descriptions.Item
              key={s.span}
              label={<Space><Tag color={s.ok ? 'default' : 'red'}>{s.span}</Tag>{ms(s.duration_ms)}</Space>}>
              <Typography.Text code style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(s.attrs, null, 1)}
              </Typography.Text>
            </Descriptions.Item>
          ))}
        </Descriptions>
      )}
    </div>
  )
}

export function AiTracesPanel() {
  const [lane, setLane] = useState<string | undefined>()
  const [outcome, setOutcome] = useState<string | undefined>()

  const { data, isFetching } = useQuery({
    queryKey: ['/admin/ai-traces', lane, outcome],
    queryFn: async () => (await api.get<{ items: TraceRow[]; stats: Stats }>(
      '/admin/ai-traces', { params: { limit: 100, lane, outcome } })).data,
    refetchInterval: 15000,
  })

  const items = data?.items ?? []
  const stats = data?.stats

  const columns: ColumnsType<TraceRow> = [
    {
      title: 'When', dataIndex: 'created_at', key: 'when', width: 150,
      render: (v: string) => (v ? String(v).replace('T', ' ').slice(0, 19) : '—'),
    },
    { title: 'Role', dataIndex: 'role', key: 'role', width: 110, render: (v) => v ?? '—' },
    { title: 'Lane', dataIndex: 'lane', key: 'lane', width: 110, render: (v) => v ?? '—' },
    {
      title: 'Question', dataIndex: 'question', key: 'q', ellipsis: true,
      render: (v: string | null) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    { title: 'Retrieval', key: 'ret', width: 220, render: (_, r) => <RetrievalCell row={r} /> },
    {
      title: 'Queued', dataIndex: 'queued_ms', key: 'qms', width: 90, align: 'right',
      // Separated from total on purpose: on a box that holds one warm model,
      // "the assistant is slow" is often two people asking at once rather than
      // a slow model, and those have opposite fixes.
      render: (v: number | null) => (v ? ms(v) : '—'),
    },
    { title: 'Total', dataIndex: 'duration_ms', key: 'ms', width: 90, align: 'right', render: ms },
    {
      title: 'Outcome', dataIndex: 'outcome', key: 'out', width: 110,
      render: (v: string | null) => <Tag color={OUTCOME_COLOR[v ?? ''] ?? 'default'}>{v ?? '—'}</Tag>,
    },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Every AI request, stage by stage. <strong>Retrieval</strong> shows which
        manual chapters the question actually reached and what they scored —
        the number that says whether a bad answer was the search or the model.
        Chapter <em>numbers</em> only: the passages themselves are never stored
        here, because that would put manual text behind a looser lock than the
        manual has.
      </Typography.Paragraph>

      {stats && !stats.enabled && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="Tracing is switched off (GI_AI_TRACE=0) — this list will not grow." />
      )}
      {stats && stats.dropped > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={`${stats.dropped} span(s) dropped`}
          description="The trace queue filled up, which means the writer is behind or stopped. Spans are dropped rather than allowed to block a request — so this list is incomplete, not the assistant." />
      )}

      <Space style={{ marginBottom: 12 }} wrap>
        <Select allowClear placeholder="Lane" style={{ width: 180 }} value={lane}
          onChange={setLane}
          options={[{ value: 'assistant', label: 'assistant' }]} />
        <Select allowClear placeholder="Outcome" style={{ width: 180 }} value={outcome}
          onChange={setOutcome}
          options={['ok', 'greeting', 'error', 'disabled'].map((v) => ({ value: v, label: v }))} />
        {stats && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            queue {stats.queued}/{stats.capacity} · written {stats.written}
            {stats.draining ? '' : ' · ⚠️ drain not running'}
          </Typography.Text>
        )}
      </Space>

      <Card size="small">
        <Table<TraceRow>
          rowKey="trace_id"
          size="small"
          loading={isFetching}
          dataSource={items}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          expandable={{ expandedRowRender: (r) => <SpanDetail row={r} /> }}
          locale={{ emptyText: 'No AI requests recorded yet — ask the Hub Assistant something.' }}
        />
      </Card>
    </div>
  )
}

export default function AiTracesPage() {
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>AI Traces</Typography.Title>
      <AiTracesPanel />
    </div>
  )
}
