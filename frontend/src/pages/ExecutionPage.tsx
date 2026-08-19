/**
 * Phase 5 — the consumption workflow: SK → Supervisor → HOD.
 *
 * One page, three views, because the three roles look at the SAME entry and
 * each is allowed to change a different part of it:
 *
 *  · a STORE KEEPER opens an entry and records what physically left the store;
 *  · a SUPERVISOR reports the area and the crew — and sees the material lines
 *    READ-ONLY. That is the control, not an oversight: they are measured
 *    against that consumption, so the person it reflects on must not be able
 *    to tidy it;
 *  · an HOD may correct either side, and the moment any number changes a
 *    justification becomes mandatory and the supervisor is notified.
 *
 * ⚠️ The lining-system field disappears for a system-agnostic activity
 * (blasting, buffing). Surface prep belongs to no lining system, and forcing a
 * choice would trap the hours under whichever system was guessed — so the form
 * submits '' and the API stores '' (a real value; NULL would break both the
 * key and every GROUP BY over it).
 */
import { PlusOutlined } from '@ant-design/icons'
import {
  Alert, App, Button, Card, Col, Descriptions, Divider, Form, Input, InputNumber,
  Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

type Row = Record<string, unknown>

const errMsg = (e: unknown): string => {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

const STATUS_META: Record<string, { color: string; label: string }> = {
  DRAFT_SK: { color: 'default', label: 'Draft (store keeper)' },
  PENDING_SUPERVISOR: { color: 'gold', label: 'With supervisor' },
  PENDING_HOD: { color: 'blue', label: 'With HOD' },
  APPROVED: { color: 'green', label: 'Approved' },
  REJECTED: { color: 'red', label: 'Rejected' },
}

const num = (v: unknown) => (v == null ? null : Number(v))
const pct = (v: unknown) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v)}%`)

/** A variance reads green only when it is genuinely comparable and small. */
function VarianceTag({ value }: { value: unknown }) {
  if (value == null) {
    return (
      <Tooltip title="No benchmark to compare against — that is not the same as
        a perfect match, so it is deliberately not shown as 0%.">
        <Tag>not comparable</Tag>
      </Tooltip>)
  }
  const n = Number(value)
  const color = Math.abs(n) <= 5 ? 'green' : Math.abs(n) <= 15 ? 'gold' : 'red'
  return <Tag color={color}>{pct(n)}</Tag>
}

function useEntries(status?: string) {
  return useQuery({
    queryKey: ['/execution/entries', status ?? ''],
    queryFn: async () => (await api.get<{ items: Row[] }>('/execution/entries',
      { params: status ? { status } : {} })).data.items,
  })
}

function useActivities() {
  return useQuery({
    queryKey: ['/execution/activities'],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/execution/activities')).data.items,
  })
}

// ─── SK: open an entry ───────────────────────────────────────────────────────
function OpenEntryModal({ open, onClose, asSupervisor }: {
  open: boolean; onClose: () => void; asSupervisor: boolean
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const acts = useActivities()
  const [activity, setActivity] = useState<Row | null>(null)
  const [materials, setMaterials] = useState<Row[]>([])

  // A supervisor may only open what needs no store keeper.
  const options = useMemo(() => ((acts.data ?? []) as Row[])
    .filter((a: Row) => !asSupervisor || a.manpower_only)
    .map((a: Row) => ({
      value: `${a.Lining_System_Code}|${a.Execution_Sub_Activity_Code}|${a.Variant_Key ?? ''}`,
      label: `${a.Execution_Sub_Activity_Code} — ${a.Sub_Activity || a.Activity}`
        + (a.Variant_Key ? ` (${a.Variant_Key})` : ''),
      row: a,
    })), [acts.data, asSupervisor])

  const create = useMutation({
    mutationFn: (b: Row) => api.post('/execution/entries', b).then((r) => r.data),
    onSuccess: () => {
      message.success('Entry opened')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      form.resetFields(); setMaterials([]); setActivity(null); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const agnostic = !!activity?.system_agnostic

  const submit = async () => {
    const v = await form.validateFields()
    create.mutate({
      work_date: v.work_date,
      equipment_tag: v.equipment_tag,
      // '' for surface prep — see the file header.
      lining_system_code: agnostic ? '' : String(activity?.Lining_System_Code ?? ''),
      execution_sub_activity_code: String(activity?.Execution_Sub_Activity_Code ?? ''),
      variant_key: String(activity?.Variant_Key ?? ''),
      materials: asSupervisor ? [] : materials,
    })
  }

  return (
    <Modal open={open} onCancel={onClose} onOk={submit} width={720}
      confirmLoading={create.isPending} destroyOnHidden
      title={asSupervisor ? 'Open a labour-only entry' : 'Open an execution entry'}>
      {asSupervisor && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="Labour-only activities skip the store keeper"
          description="Blasting and buffing consume no Surface Shield, so there
            is nothing for a store keeper to count and no draft for them to
            raise. Only those activities are listed here." />
      )}
      <Form form={form} layout="vertical">
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="work_date" label="Work date" rules={[{ required: true }]}>
              <Input type="date" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="equipment_tag" label="Equipment tag"
              rules={[{ required: true }]}><Input placeholder="522-8J80-TNK-031" /></Form.Item>
          </Col>
        </Row>
        <Form.Item label="Activity" required>
          <Select showSearch options={options} loading={acts.isFetching}
            placeholder="Sub-activity"
            onChange={(v) => setActivity(options.find((o: { value: string; row: Row }) => o.value === v)?.row ?? null)}
            optionFilterProp="label" />
        </Form.Item>
        {activity && (
          agnostic
            ? <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message="No lining system for this activity"
                description="Surface prep belongs to no lining system. Tying
                  these hours to one would trap them there if the lining plan
                  changes, so the entry is recorded against the equipment only." />
            : <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
                <Descriptions.Item label="Lining system">
                  {String(activity.Lining_System_Code)}
                </Descriptions.Item>
                <Descriptions.Item label="Benchmark">
                  {String(activity.Crew_Size)} crew @{' '}
                  {String(activity.Standard_Productivity_Per_Shift)} m²/shift
                </Descriptions.Item>
              </Descriptions>
        )}
        {!asSupervisor && (
          <>
            <Divider plain>Material consumed</Divider>
            {materials.map((m, i) => (
              <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
                <Input placeholder="Material code" value={String(m.Material_Code ?? '')}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, Material_Code: e.target.value } : x))} />
                <Input placeholder="SAP" value={String(m.SAP_Code ?? '')}
                  style={{ width: 110 }}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, SAP_Code: e.target.value } : x))} />
                <InputNumber placeholder="Qty" min={0} value={num(m.Actual_Qty)}
                  onChange={(v) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, Actual_Qty: v ?? 0 } : x))} />
                <Input placeholder="UOM" value={String(m.UOM ?? '')} style={{ width: 80 }}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, UOM: e.target.value } : x))} />
                <Button danger onClick={() =>
                  setMaterials((r) => r.filter((_, j) => j !== i))}>Remove</Button>
              </Space>
            ))}
            <Button icon={<PlusOutlined />} onClick={() =>
              setMaterials((r) => [...r, { Material_Code: '', SAP_Code: '', Actual_Qty: 0, UOM: '' }])}>
              Add material line
            </Button>
          </>
        )}
      </Form>
    </Modal>
  )
}

// ─── Supervisor: the area, the crew, and the two mandatory reasons ───────────
function SupervisorModal({ entry, onClose }: { entry: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [crew, setCrew] = useState<Row[]>([])
  const roles = useQuery({
    queryKey: ['/sme/master/roles'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/sme/master/roles')).data.items,
  })

  const save = useMutation({
    mutationFn: (b: Row) =>
      api.post(`/execution/entries/${entry?.id}/supervisor`, b).then((r) => r.data),
    onSuccess: () => {
      message.success('Sent to the HOD')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      form.resetFields(); setCrew([]); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const submit = async () => {
    const v = await form.validateFields()
    save.mutate({
      actual_sqm: v.actual_sqm,
      manpower: crew.filter((c) => c.Role_Code),
      material_variance_reason: v.material_variance_reason,
      manpower_variance_reason: v.manpower_variance_reason,
    })
  }

  return (
    <Modal open={!!entry} onCancel={onClose} onOk={submit} width={760}
      confirmLoading={save.isPending} destroyOnHidden
      title={entry ? `Report execution — ${entry.Entry_No}` : ''}>
      {entry && (
        <>
          <Descriptions size="small" column={3} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="Equipment">{String(entry.Equipment_Tag_No)}</Descriptions.Item>
            <Descriptions.Item label="System">
              {String(entry.Lining_System_Code) || <Tag>surface prep</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Sub-activity">
              {String(entry.Execution_Sub_Activity_Code)}
            </Descriptions.Item>
          </Descriptions>

          {(entry.materials as Row[])?.length > 0 && (
            <>
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                message="Material lines are read-only here"
                description="The store keeper counted what left the store. Your
                  figures are measured against it, so it is not yours to edit —
                  ask the HOD if it is wrong." />
              <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
                style={{ marginBottom: 12 }}
                dataSource={entry.materials as Row[]}
                columns={[
                  { title: 'Material', dataIndex: 'Material_Code' },
                  { title: 'SAP', dataIndex: 'SAP_Code' },
                  { title: 'Qty', dataIndex: 'Actual_Qty', align: 'right' },
                  { title: 'UOM', dataIndex: 'UOM' },
                ]} />
            </>
          )}

          <Form form={form} layout="vertical">
            <Form.Item name="actual_sqm" label="Actual area done (m²)"
              rules={[{ required: true, message: 'the area is required' }]}>
              <InputNumber min={0.01} step={1} style={{ width: 220 }} />
            </Form.Item>

            <Divider plain>Crew that did the work</Divider>
            {crew.map((c, i) => (
              <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
                <Select style={{ width: 220 }} placeholder="Role"
                  value={c.Role_Code ? String(c.Role_Code) : undefined}
                  options={(roles.data ?? []).map((r: Row) => ({
                    value: String(r.Role_Code), label: String(r.Name) }))}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Role_Code: v } : y))} />
                <InputNumber placeholder="Head" min={0} value={num(c.Headcount)}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Headcount: v ?? 0 } : y))} />
                <InputNumber placeholder="Hours each" min={0} step={0.5}
                  value={num(c.Hours)}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Hours: v ?? 0 } : y))} />
                <Button danger onClick={() =>
                  setCrew((x) => x.filter((_, j) => j !== i))}>Remove</Button>
              </Space>
            ))}
            <Button icon={<PlusOutlined />} style={{ marginBottom: 12 }}
              onClick={() => setCrew((x) => [...x, { Role_Code: '', Headcount: 0, Hours: 11 }])}>
              Add crew line
            </Button>

            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message="Both reasons are required, even at zero variance"
              description="A reason demanded only past a threshold teaches
                people to aim just under it. A zero-variance entry with a stated
                reason is evidence you looked at the comparison." />
            <Form.Item name="material_variance_reason" label="Reason — material variance"
              rules={[{ required: true, message: 'required on every entry' }]}>
              <Input.TextArea rows={2} />
            </Form.Item>
            <Form.Item name="manpower_variance_reason" label="Reason — manpower variance"
              rules={[{ required: true, message: 'required on every entry' }]}>
              <Input.TextArea rows={2} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

// ─── HOD: review, correct, decide ────────────────────────────────────────────
function HodModal({ entry, onClose }: { entry: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [sqm, setSqm] = useState<number | null>(null)
  const [mats, setMats] = useState<Record<number, number>>({})
  const [crew, setCrew] = useState<Record<number, { Headcount?: number; Hours?: number }>>({})
  const [why, setWhy] = useState('')
  const [reject, setReject] = useState('')

  const v = entry?.variance as Row | undefined
  const mv = v?.material_total as Row | undefined
  const pv = v?.manpower as Row | undefined

  const decide = useMutation({
    mutationFn: (b: Row) =>
      api.post(`/execution/entries/${entry?.id}/decision`, b).then((r) => r.data),
    onSuccess: (_d, b) => {
      message.success((b as Row).approve ? 'Approved' : 'Rejected')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      setSqm(null); setMats({}); setCrew({}); setWhy(''); setReject(''); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const edited = sqm != null || Object.keys(mats).length > 0
    || Object.keys(crew).length > 0

  return (
    <Modal open={!!entry} onCancel={onClose} destroyOnHidden width={860}
      title={entry ? `Approve execution — ${entry.Entry_No}` : ''}
      footer={entry && [
        <Popconfirm key="r" title="Reject this entry?"
          description={<Input.TextArea rows={2} value={reject} placeholder="Reason"
            onChange={(e) => setReject(e.target.value)} />}
          onConfirm={() => decide.mutate({ approve: false, reject_reason: reject })}>
          <Button danger>Reject</Button>
        </Popconfirm>,
        <Button key="a" type="primary" loading={decide.isPending}
          onClick={() => decide.mutate({
            approve: true,
            justification: why,
            actual_sqm: sqm ?? undefined,
            materials: Object.entries(mats).map(([id, q]) =>
              ({ id: Number(id), Actual_Qty: q })),
            manpower: Object.entries(crew).map(([id, c]) => ({ id: Number(id), ...c })),
          })}>
          {edited ? 'Approve with corrections' : 'Approve'}
        </Button>,
      ]}>
      {entry && (
        <>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={8}><Card size="small">
              <Statistic title="Area reported" value={String(entry.Actual_SQM ?? '—')} suffix="m²" />
            </Card></Col>
            <Col span={8}><Card size="small">
              <Statistic title="Material vs benchmark"
                value={String(mv?.Actual ?? '—')}
                suffix={<span style={{ fontSize: 13 }}>
                  / {String(mv?.Benchmark ?? '—')} <VarianceTag value={mv?.Variance_Pct} />
                </span>} />
            </Card></Col>
            <Col span={8}><Card size="small">
              <Statistic title="Man-hours vs benchmark"
                value={String(pv?.Actual_Manhours ?? '—')}
                suffix={<span style={{ fontSize: 13 }}>
                  / {String(pv?.Benchmark_Manhours ?? '—')} <VarianceTag value={pv?.Variance_Pct} />
                </span>} />
            </Card></Col>
          </Row>

          <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="Supervisor — material reason">
              {String(entry.Material_Variance_Reason ?? '—')}
            </Descriptions.Item>
            <Descriptions.Item label="Supervisor — manpower reason">
              {String(entry.Manpower_Variance_Reason ?? '—')}
            </Descriptions.Item>
          </Descriptions>

          <Divider plain>Material (store keeper) — editable</Divider>
          <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
            dataSource={(v?.materials as Row[]) ?? []}
            columns={[
              { title: 'Material', dataIndex: 'Material_Code' },
              { title: 'Benchmark', dataIndex: 'Benchmark_Qty', align: 'right',
                render: (x) => x ?? '—' },
              { title: 'Actual', dataIndex: 'Actual_Qty', align: 'right' },
              { title: 'Variance', dataIndex: 'Variance_Pct', align: 'right',
                render: (x) => <VarianceTag value={x} /> },
              { title: 'Correct to', key: 'e', width: 140,
                render: (_: unknown, r: Row) => {
                  const line = (entry.materials as Row[])
                    ?.find((m: Row) => m.Material_Code === r.Material_Code)
                  return (
                    <InputNumber min={0} placeholder={String(r.Actual_Qty)}
                      value={line ? mats[Number(line.id)] : undefined}
                      onChange={(x) => line && setMats((s) => {
                        const n = { ...s }
                        if (x == null) delete n[Number(line.id)]
                        else n[Number(line.id)] = Number(x)
                        return n
                      })} />)
                } },
            ]} />

          <Divider plain>Crew (supervisor) — editable</Divider>
          <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
            dataSource={(entry.manpower as Row[]) ?? []}
            columns={[
              { title: 'Role', dataIndex: 'Role_Code' },
              { title: 'Benchmark head', dataIndex: 'Bench_Headcount',
                align: 'right', render: (x) => x ?? '—' },
              { title: 'Head', dataIndex: 'Headcount', align: 'right' },
              { title: 'Hours', dataIndex: 'Hours', align: 'right' },
              { title: 'Correct head', key: 'h', width: 130,
                render: (_: unknown, r: Row) => (
                  <InputNumber min={0} placeholder={String(r.Headcount)}
                    value={crew[Number(r.id)]?.Headcount}
                    onChange={(x) => setCrew((s) => ({ ...s,
                      [Number(r.id)]: { ...s[Number(r.id)], Headcount: x ?? undefined } }))} />) },
              { title: 'Correct hours', key: 'hr', width: 130,
                render: (_: unknown, r: Row) => (
                  <InputNumber min={0} step={0.5} placeholder={String(r.Hours)}
                    value={crew[Number(r.id)]?.Hours}
                    onChange={(x) => setCrew((s) => ({ ...s,
                      [Number(r.id)]: { ...s[Number(r.id)], Hours: x ?? undefined } }))} />) },
            ]} />

          <Form layout="vertical" style={{ marginTop: 12 }}>
            <Form.Item label="Correct the area (m²)">
              <InputNumber min={0} value={sqm} onChange={setSqm}
                placeholder={String(entry.Actual_SQM ?? '')} style={{ width: 200 }} />
            </Form.Item>
            {edited && (
              <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                message="You are changing somebody else's figures"
                description="A justification is required, and the supervisor is
                  notified of exactly what changed. Without it they would be
                  answering for numbers they never entered." />
            )}
            <Form.Item label="Justification for your corrections"
              required={edited} validateStatus={edited && !why.trim() ? 'error' : undefined}
              help={edited && !why.trim() ? 'required once you change a number' : undefined}>
              <Input.TextArea rows={2} value={why}
                onChange={(e) => setWhy(e.target.value)} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

// ─── the page ────────────────────────────────────────────────────────────────
export default function ExecutionPage() {
  const { user } = useAuth()
  const role = user?.role ?? ''
  const isSk = role === 'store_keeper'
  const isSup = role === 'supervisor'
  const isHod = role === 'hod' || role === 'admin'

  const { data, isFetching } = useEntries()
  const [openNew, setOpenNew] = useState(false)
  const [supRow, setSupRow] = useState<Row | null>(null)
  const [hodRow, setHodRow] = useState<Row | null>(null)
  const { message } = App.useApp()
  const qc = useQueryClient()

  const submitSk = useMutation({
    mutationFn: (id: number) =>
      api.post(`/execution/entries/${id}/submit`).then((r) => r.data),
    onSuccess: () => {
      message.success('Sent to the supervisor')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const rows = data ?? []
  const columns: ColumnsType<Row> = [
    { title: 'Entry', dataIndex: 'Entry_No', width: 150 },
    { title: 'Date', dataIndex: 'Work_Date', width: 110 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 170 },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 120,
      render: (v: string) => v || <Tag>surface prep</Tag> },
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 130 },
    { title: 'Area', dataIndex: 'Actual_SQM', width: 90, align: 'right',
      render: (v) => (v == null ? '—' : `${Number(v)} m²`) },
    { title: 'Material', key: 'mv', width: 110, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.material_total as Row)?.Variance_Pct} /> },
    { title: 'Manpower', key: 'pv', width: 110, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.manpower as Row)?.Variance_Pct} /> },
    { title: 'Status', dataIndex: 'status', width: 160,
      render: (v: string, r: Row) => (
        <Space size={4}>
          <Tag color={STATUS_META[v]?.color}>{STATUS_META[v]?.label ?? v}</Tag>
          {r.hod_edited ? (
            <Tooltip title={String(r.HOD_Edit_Justification ?? '')}>
              <Tag color="purple">HOD edited</Tag>
            </Tooltip>) : null}
        </Space>) },
    {
      title: 'Action', key: 'a', fixed: 'right', width: 170,
      render: (_: unknown, r: Row) => {
        const st = String(r.status)
        if (isSk && st === 'DRAFT_SK') {
          return <Button size="small" type="primary" loading={submitSk.isPending}
            onClick={() => submitSk.mutate(Number(r.id))}>Send to supervisor</Button>
        }
        if (isSup && st === 'PENDING_SUPERVISOR') {
          return <Button size="small" type="primary"
            onClick={() => setSupRow(r)}>Report area & crew</Button>
        }
        if (isHod && st === 'PENDING_HOD') {
          return <Button size="small" type="primary"
            onClick={() => setHodRow(r)}>Review</Button>
        }
        return <Button size="small" onClick={() => setHodRow(r)}>View</Button>
      },
    },
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Execution entries</Typography.Title>
      <Typography.Paragraph type="secondary">
        The store keeper records what left the store, the supervisor reports the
        area and crew, and the HOD approves — approval is what deducts stock, so
        nothing before it moves a quantity.
      </Typography.Paragraph>
      {(isSk || isSup) && (
        <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 12 }}
          onClick={() => setOpenNew(true)}>
          {isSup ? 'Open a labour-only entry' : 'Open an entry'}
        </Button>
      )}
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={rows} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true,
                      showTotal: (t) => `${t} entries` }} />
      <OpenEntryModal open={openNew} onClose={() => setOpenNew(false)}
        asSupervisor={isSup} />
      <SupervisorModal entry={supRow} onClose={() => setSupRow(null)} />
      <HodModal entry={hodRow} onClose={() => setHodRow(null)} />
    </div>
  )
}
