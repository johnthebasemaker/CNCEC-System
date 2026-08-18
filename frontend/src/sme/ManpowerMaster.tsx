/**
 * SME Phase 7 — Manpower master: the productivity benchmarks and the role
 * vocabulary they are expressed in.
 *
 * Two things here are not obvious from the grid:
 *
 * 1. A benchmark's identity is FIVE parts, not the (system, sub-activity) pair
 *    it looks like. CV blasting is filed under ESC1 twice — 300 m²/shift with a
 *    crew of 4, and 40 m²/shift with a crew of 2 — and nothing narrower tells
 *    them apart, so `Variant_Key` is the column that stops one silently
 *    replacing the other.
 *
 * 2. A norm with no matching recipe line is MANPOWER-ONLY (blasting, buffing).
 *    That is a real category, not missing data: those activities consume no
 *    Surface Shield, and a supervisor opens them without a store keeper. The
 *    badge exists so nobody "fixes" it by inventing a material.
 */
import { DeleteOutlined, PlusOutlined, TeamOutlined } from '@ant-design/icons'
import {
  Alert, App, Button, Card, Col, Form, Input, InputNumber, Modal, Popconfirm,
  Row as GridRow, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'

import {
  useSmeMasterCreate, useSmeMasterDelete, useSmeMasterList, useSmeMasterPatch,
} from '../api/hooks'

type Row = Record<string, unknown>

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

const NUM = (v: unknown) => (v == null ? 0 : Number(v))
const fmt = (v: unknown) => {
  const n = NUM(v)
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

/** Edit a benchmark's crew — role by role, against the role master. */
function CrewModal({ row, roles, onClose, onSaved }: {
  row: Row | null
  roles: Record<string, string>
  onClose: () => void
  onSaved: () => void
}) {
  const { message } = App.useApp()
  const patch = useSmeMasterPatch('manpower-norms')
  const [crew, setCrew] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setCrew({ ...((row?.Crew as Record<string, number>) ?? {}) })
  }, [row?.id])   // eslint-disable-line react-hooks/exhaustive-deps

  const total = Object.values(crew).reduce((a, b) => a + Number(b || 0), 0)
  const stated = NUM(row?.Crew_Size)
  const mismatch = row != null && Math.abs(total - stated) > 1e-9

  const save = async () => {
    if (!row) return
    setBusy(true)
    try {
      await patch.mutateAsync({ id: row.id as number, body: { Crew: crew } })
      message.success('Crew saved')
      onSaved()
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={!!row} onCancel={onClose} destroyOnHidden width={520}
      title={row ? `Crew — ${row.Type} / ${row.Lining_System_Code} / ${row.Execution_Sub_Activity_Code}` : ''}
      onOk={save} confirmLoading={busy}>
      {row && (
        <>
          <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
            How many of each role this benchmark assumes. Setting a role to 0
            removes it from the crew.
          </Typography.Paragraph>
          {mismatch && (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message={`Crew adds up to ${fmt(total)}, but the benchmark states ${fmt(stated)}`}
              description="Neither is corrected automatically — the stated
                figure is what the workbook shipped, and overwriting it here
                would hide a disagreement the planner needs to surface." />
          )}
          <GridRow gutter={[12, 8]}>
            {Object.entries(roles).map(([code, name]) => (
              <Col span={12} key={code}>
                <Space.Compact style={{ width: '100%' }}>
                  <Input disabled value={name} style={{ width: '62%' }} />
                  <InputNumber min={0} step={1} style={{ width: '38%' }}
                    value={crew[code] ?? 0}
                    onChange={(v) => setCrew((c) => ({ ...c, [code]: Number(v ?? 0) }))} />
                </Space.Compact>
              </Col>
            ))}
          </GridRow>
        </>
      )}
    </Modal>
  )
}

const NORM_FIELDS = [
  { name: 'Type', label: 'Type (CV / ME)', required: true },
  { name: 'Lining_System_Code', label: 'Lining System Code', required: true },
  { name: 'Execution_Sub_Activity_Code', label: 'Sub-Activity Code', required: true },
  { name: 'Activity', label: 'Activity', required: true },
  { name: 'Variant_Key', label: 'Variant Key (only when two rows would collide)' },
  { name: 'Sub_Activity', label: 'Sub-Activity' },
  { name: 'System', label: 'System' },
  { name: 'Activity_Code', label: 'Activity Code #' },
  { name: 'Crew_Size', label: 'Crew size', number: true },
  { name: 'Hours_Per_Shift', label: 'Hours / shift', number: true },
  { name: 'Manhours_Per_Shift', label: 'Man-hours / shift', number: true },
  { name: 'Standard_Productivity_Per_Shift', label: 'Standard productivity / shift', number: true },
  { name: 'SQM_Per_Hour_Per_Person', label: 'm² / hr / person', number: true },
  { name: 'Remarks', label: 'Remarks' },
]

export function ManpowerNormsTab() {
  const { message } = App.useApp()
  const list = useSmeMasterList('manpower-norms')
  const create = useSmeMasterCreate('manpower-norms')
  const patch = useSmeMasterPatch('manpower-norms')
  const del = useSmeMasterDelete('manpower-norms')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Row | null>(null)
  const [crewRow, setCrewRow] = useState<Row | null>(null)
  const [form] = Form.useForm()

  const rows = list.data ?? []
  // The role vocabulary comes from its own endpoint rather than riding on this
  // response: `useSmeMasterList` unwraps to `items`, so an envelope field would
  // be silently dropped and the crew editor would show role CODES to a user.
  const roleList = useSmeMasterList('roles')
  const roles = useMemo(() => Object.fromEntries(
    (roleList.data ?? [])
      .filter((r) => (r as Row).status !== 'inactive')
      .map((r) => [String((r as Row).Role_Code), String((r as Row).Name)])),
    [roleList.data])

  const openAdd = () => { setEditing(null); form.resetFields(); setOpen(true) }
  const openEdit = (r: Row) => {
    setEditing(r)
    form.resetFields()
    form.setFieldsValue(Object.fromEntries(NORM_FIELDS.map((f) => [f.name, r[f.name]])))
    setOpen(true)
  }

  const submit = async () => {
    const values = (await form.validateFields()) as Row
    const body: Row = {}
    for (const f of NORM_FIELDS) {
      const v = values[f.name]
      if (v !== undefined && v !== null && v !== '') body[f.name] = v
    }
    try {
      if (editing) {
        await patch.mutateAsync({ id: editing.id as number, body })
        message.success('Updated')
      } else {
        await create.mutateAsync(body)
        message.success('Saved')
      }
      setOpen(false)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = useMemo(() => [
    { title: 'Type', dataIndex: 'Type', width: 70,
      filters: [{ text: 'CV', value: 'CV' }, { text: 'ME', value: 'ME' }],
      onFilter: (v, r) => r.Type === v },
    { title: 'System Code', dataIndex: 'Lining_System_Code', width: 120 },
    { title: 'Sub-Activity Code', dataIndex: 'Execution_Sub_Activity_Code', width: 140 },
    { title: 'Activity', dataIndex: 'Activity', width: 200,
      render: (v: string, r: Row) => (
        <Space size={4} wrap>
          <span>{v}</span>
          {r.Variant_Key ? <Tag color="purple">{String(r.Variant_Key)}</Tag> : null}
          {r.Manpower_Only
            ? <Tooltip title="No Surface Shield recipe for this system + sub-activity.
                This activity is labour only — a supervisor can open it without a
                store keeper.">
                <Tag color="gold">manpower only</Tag>
              </Tooltip>
            : null}
        </Space>) },
    { title: 'Sub-Activity', dataIndex: 'Sub_Activity', width: 170 },
    { title: 'Crew', dataIndex: 'Crew_Text', width: 240,
      render: (v: string, r: Row) => (
        <Space size={4}>
          <span>{v || '—'}</span>
          <Tooltip title="Edit the crew composition">
            <Button size="small" icon={<TeamOutlined />} onClick={() => setCrewRow(r)} />
          </Tooltip>
        </Space>) },
    { title: 'Crew size', dataIndex: 'Crew_Size', width: 90, align: 'right',
      render: fmt },
    { title: 'Hrs/shift', dataIndex: 'Hours_Per_Shift', width: 90, align: 'right',
      render: fmt },
    { title: 'Man-hrs/shift', dataIndex: 'Manhours_Per_Shift', width: 110,
      align: 'right', render: fmt },
    { title: 'Std prod./shift', dataIndex: 'Standard_Productivity_Per_Shift',
      width: 120, align: 'right', render: fmt },
    { title: 'm²/hr/person', dataIndex: 'SQM_Per_Hour_Per_Person', width: 110,
      align: 'right', render: fmt },
    { title: 'Remarks', dataIndex: 'Remarks', width: 150 },
    {
      title: 'Actions', key: '__a', fixed: 'right', width: 150,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(r)}>Edit</Button>
          <Popconfirm title="Delete this benchmark? Its crew rows go with it."
            onConfirm={async () => {
              try {
                await del.mutateAsync({ id: r.id as number })
                message.success('Deleted')
              } catch (e) { message.error(errMsg(e)) }
            }}>
            <Button size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [rows])

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Productivity benchmarks from <code>Manpower_Hour_Details.xlsx</code>.
        A benchmark is identified by <b>Type + System Code + Sub-Activity Code +
        Activity + Variant Key</b> — the last two exist because the workbook
        files two different blasting crews under one code, and because one seal-coat
        code serves both the 4 mm and 6 mm PU systems.
      </Typography.Paragraph>
      <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}
        style={{ marginBottom: 12 }}>Add benchmark</Button>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={list.isFetching}
        columns={columns} dataSource={rows.map((r, i) => ({ ...r, __rk: i }))}
        rowKey="__rk" scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} benchmarks` }} />
      <Modal open={open} forceRender destroyOnHidden
        title={editing ? 'Edit benchmark' : 'Add benchmark'}
        onCancel={() => setOpen(false)} onOk={submit}
        confirmLoading={create.isPending || patch.isPending} width={720}>
        <Form form={form} layout="vertical">
          <GridRow gutter={12}>
            {NORM_FIELDS.map((f) => (
              <Col span={12} key={f.name}>
                <Form.Item name={f.name} label={f.label}
                  rules={f.required ? [{ required: true, message: `${f.label} is required` }] : []}>
                  {f.number
                    ? <InputNumber style={{ width: '100%' }} min={0} />
                    : <Input />}
                </Form.Item>
              </Col>
            ))}
          </GridRow>
        </Form>
      </Modal>
      <CrewModal row={crewRow} roles={roles} onClose={() => setCrewRow(null)}
        onSaved={() => list.refetch()} />
    </div>
  )
}

export function RolesTab() {
  const { message } = App.useApp()
  const list = useSmeMasterList('roles')
  const create = useSmeMasterCreate('roles')
  const del = useSmeMasterDelete('roles')
  const [code, setCode] = useState('')
  const [name, setName] = useState('')

  const rows = list.data ?? []

  const add = async () => {
    if (!code.trim() || !name.trim()) return
    try {
      await create.mutateAsync({ Role_Code: code.trim(), Name: name.trim(),
                                 Sort_Order: 100 })
      message.success('Role added')
      setCode(''); setName('')
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Code', dataIndex: 'Role_Code', width: 180 },
    { title: 'Name', dataIndex: 'Name', width: 200 },
    { title: 'Source', dataIndex: 'Source', width: 120,
      render: (v: string) => v === 'workbook'
        ? <Tooltip title="Comes from Manpower_Hour_Details.xlsx — the vocabulary
            the benchmarks are written in. Rename it in the workbook, not here.">
            <Tag color="blue">workbook</Tag></Tooltip>
        : <Tag>custom</Tag> },
    { title: 'In use', dataIndex: 'In_Use', width: 90,
      render: (v: boolean) => v ? <Tag color="green">yes</Tag> : <Tag>no</Tag> },
    {
      title: 'Actions', key: '__a', width: 120,
      render: (_: unknown, r: Row) => (
        <Popconfirm title="Delete this role?" disabled={r.Source === 'workbook'}
          onConfirm={async () => {
            try {
              await del.mutateAsync({ id: r.id as number })
              message.success('Deleted')
            } catch (e) { message.error(errMsg(e)) }
          }}>
          <Button size="small" danger icon={<DeleteOutlined />}
            disabled={r.Source === 'workbook'} />
        </Popconfirm>),
    },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        The roles every crew figure and every roster entry is expressed in.
        Workbook roles are read-only here; add your own for anything the
        workbook does not name.
      </Typography.Paragraph>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input placeholder="Role code (e.g. SCAFFOLDER)" value={code}
            onChange={(e) => setCode(e.target.value)} style={{ width: '35%' }} />
          <Input placeholder="Display name (e.g. Scaffolder)" value={name}
            onChange={(e) => setName(e.target.value)} style={{ width: '45%' }}
            onPressEnter={add} />
          <Button type="primary" icon={<PlusOutlined />} loading={create.isPending}
            onClick={add} style={{ width: '20%' }}>Add role</Button>
        </Space.Compact>
      </Card>
      <Table size="small" loading={list.isFetching} columns={columns}
        dataSource={rows.map((r, i) => ({ ...r, __rk: i }))} rowKey="__rk"
        pagination={false} />
    </div>
  )
}
