/**
 * QcInspectionsPage — the quality inspector's queue, and everyone else's
 * window into why a Store Keeper's issue was refused.
 *
 * The page is readable by six roles and decidable by one. That asymmetry is
 * deliberate: an SK who is blocked at the issue form needs to see the row
 * that blocked them (and who has it), an HOD needs to chase it, and an
 * Auditor needs to audit it — but only a `qc` may approve. The Decide button
 * is therefore gated on the role rather than the page being hidden, which is
 * also why the nav entry is NOT marked `writes`.
 *
 * Scoping is entirely server-side. This page never sends a site or a
 * warehouse: `auth.qc_scope()` decides what the caller may read, so a site
 * inspector cannot reach another site's queue by editing a query string.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Descriptions, Empty, InputNumber, Input, Modal,
  Segmented, Space, Tag, Typography,
} from 'antd'
import { ExperimentOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import { useQcDecide, useQcInspections } from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'gold', label: 'Awaiting inspection' },
  approved: { color: 'green', label: 'Approved' },
  partially_approved: { color: 'orange', label: 'Partly approved' },
  rejected: { color: 'red', label: 'Rejected' },
}

const num = (v: unknown) => Number(v ?? 0)

/**
 * The decision. One modal for approve / partly approve / reject, because they
 * differ only in the quantity — and modelling them as three buttons is how
 * two of the three end up forgetting to ask for a reason.
 */
function DecideModal({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const decide = useQcDecide()
  const submitted = num(row?.submitted_qty)
  const [qty, setQty] = useState<number>(submitted)
  const [reason, setReason] = useState('')

  const rejecting = submitted - qty > 1e-9
  const outcome = qty <= 1e-9 ? 'rejected'
    : qty >= submitted - 1e-9 ? 'approved' : 'partially_approved'

  const submit = async () => {
    try {
      const res = await decide.mutateAsync({
        id: Number(row!.id), approved_qty: qty, reason: reason.trim() || undefined,
      })
      message.success(
        `${STATUS_TAG[String(res.status)]?.label ?? res.status} — `
        + `${num(res.approved_qty)} cleared for issue`)
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal
      open={!!row}
      title={`Inspect ${row?.SAP_Code ?? ''}`}
      onCancel={onClose}
      onOk={submit}
      okText="Record decision"
      confirmLoading={decide.isPending}
      okButtonProps={{ disabled: rejecting && !reason.trim() }}
      afterOpenChange={(open) => { if (open) { setQty(submitted); setReason('') } }}
    >
      <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="Material">
          {String(row?.SAP_Code ?? '')}
          {row?.Material_Code ? ` · ${row.Material_Code}` : ''}
        </Descriptions.Item>
        <Descriptions.Item label="Lot">{String(row?.Lot_Number ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Where">
          {String(row?.Site_ID || row?.Warehouse_ID || '—')}
        </Descriptions.Item>
        <Descriptions.Item label="Submitted">{submitted}</Descriptions.Item>
        <Descriptions.Item label="MTC">
          {row?.mtc_document_id
            ? <Tag color="green">certificate #{String(row.mtc_document_id)}</Tag>
            : <Tag>not linked</Tag>}
        </Descriptions.Item>
      </Descriptions>

      <Space orientation="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Typography.Text strong>Quantity you are approving</Typography.Text>
          <InputNumber
            style={{ width: '100%', marginTop: 4 }}
            min={0} max={submitted} value={qty}
            onChange={(v) => setQty(Number(v ?? 0))}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            The rest stays in stock as unusable — it is never auto-returned to
            the vendor, and it simply cannot be issued.
          </Typography.Text>
        </div>
        {rejecting && (
          <div>
            <Typography.Text strong>
              Reason for rejecting {Number((submitted - qty).toFixed(6))}
            </Typography.Text>
            <Input.TextArea
              rows={3} value={reason} style={{ marginTop: 4 }}
              onChange={(e) => setReason(e.target.value)}
              placeholder="What is wrong with the material or its certificate?"
            />
          </div>
        )}
        <Alert
          type={outcome === 'rejected' ? 'error' : outcome === 'approved' ? 'success' : 'warning'}
          showIcon
          title={`This records: ${STATUS_TAG[outcome].label}`}
          description={
            outcome === 'rejected'
              ? 'Nothing from this lot may be issued to the field.'
              : `${qty} unit(s) become issuable; the Store Keeper is notified.`}
        />
      </Space>
    </Modal>
  )
}

export default function QcInspectionsPage() {
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const [status, setStatus] = useState<string>('pending')
  const [decide, setDecide] = useState<Row | null>(null)
  const { data, isLoading } = useQcInspections(
    status === 'all' ? {} : { status })

  const isInspector = user?.role === 'qc'
  const rows = data ?? []
  // Not memoised: `rows` is a fresh array on every render, so a useMemo keyed
  // on it recomputes every time anyway — it would only add the appearance of
  // caching. This is a filter over at most 200 rows.
  const pendingCount = rows.filter((r) => r.status === 'pending').length

  const columns: ColumnsType<Row> = [
    {
      title: 'Status', dataIndex: 'status', key: 'status', width: 170,
      render: (v: string) => {
        const t = STATUS_TAG[v] ?? { color: 'default', label: v }
        return <Tag color={t.color}>{t.label}</Tag>
      },
    },
    { title: 'Material', dataIndex: 'SAP_Code', key: 'sap', width: 110 },
    { title: 'Material code', dataIndex: 'Material_Code', key: 'mat', ellipsis: true },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'lot', width: 120,
      render: (v) => String(v ?? '—') },
    {
      title: 'Where', key: 'place', width: 120,
      render: (_, r) => String(r.Site_ID || r.Warehouse_ID || '—'),
    },
    { title: 'Submitted', dataIndex: 'submitted_qty', key: 'sub', align: 'right',
      width: 100, render: num },
    { title: 'Approved', dataIndex: 'approved_qty', key: 'app', align: 'right',
      width: 100, render: num },
    { title: 'Rejected', dataIndex: 'rejected_qty', key: 'rej', align: 'right',
      width: 100, render: num },
    { title: 'Reason', dataIndex: 'decision_reason', key: 'why', ellipsis: true,
      render: (v) => String(v ?? '') },
    { title: 'Inspector', dataIndex: 'inspected_by', key: 'by', width: 130,
      render: (v) => String(v ?? '—') },
    {
      title: 'Action', key: '__act', width: 120, fixed: 'right',
      render: (_, r) => (
        <Button
          type="primary" size="small"
          // Visible to everyone, usable by the inspector. Six roles read this
          // page — an SK who was just refused at the issue form needs to see
          // the row and who holds it, not a 404.
          disabled={!isInspector || readOnly || r.status !== 'pending'}
          onClick={() => setDecide(r)}
        >
          Inspect
        </Button>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <ExperimentOutlined /> Quality Inspections
        </Typography.Title>
        <Typography.Text type="secondary">
          Surface-Shield material is inspected where it lands — at the warehouse
          when it is booked in, and at the site when a delivery note is
          received. Material may travel uninspected; it may not be{' '}
          <strong>issued</strong> until a quantity here is approved.
        </Typography.Text>
      </div>

      {!isInspector && (
        <Alert
          type="info" showIcon
          title="You are viewing this queue, not deciding it"
          description="Approving and rejecting is restricted to Quality Control accounts."
        />
      )}
      {isInspector && pendingCount > 0 && (
        <Alert
          type="warning" showIcon
          title={`${pendingCount} lot(s) waiting on you`}
          description="Nothing in this list can be issued to the field until it is inspected."
        />
      )}

      <Card
        size="small"
        title={
          <Segmented
            value={status}
            onChange={(v) => setStatus(String(v))}
            options={[
              { label: 'Awaiting inspection', value: 'pending' },
              { label: 'Approved', value: 'approved' },
              { label: 'Partly approved', value: 'partially_approved' },
              { label: 'Rejected', value: 'rejected' },
              { label: 'All', value: 'all' },
            ]}
          />
        }
      >
        <Table
          sticky={{ offsetHeader: 64 }}
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={rows}
          rowKey={(r) => String(r.id)}
          scroll={{ x: 'max-content' }}
          locale={{
            emptyText: (
              <Empty description={
                status === 'pending'
                  ? 'Nothing is waiting for inspection.'
                  : 'No inspections in this state.'} />
            ),
          }}
        />
      </Card>

      <DecideModal row={decide} onClose={() => setDecide(null)} />
    </Space>
  )
}
