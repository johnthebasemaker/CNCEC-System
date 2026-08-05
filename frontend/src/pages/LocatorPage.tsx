/**
 * frontend/src/pages/LocatorPage.tsx — "which rack is it in?"
 *
 * Three things on one page, because they are one job:
 *
 *   FIND      type a material name, a SAP code, or scan its QR → the shelf
 *   RACKS     create and edit the places themselves
 *   CONTENTS  scan a RACK's own QR → what is meant to be on it
 *
 * That last one is the cheap half nobody asks for and everybody uses: the
 * lookup index answers both directions, so "what is supposed to be here" costs
 * nothing extra and turns a stock count from a hunt into a checklist.
 *
 * Reads are open to every authenticated role — a store keeper is level 0 and
 * is exactly who needs this. The write controls are hidden for a read-only
 * role via `useReadOnly()`; the server refuses them regardless.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Col, Empty, Form, Input, Modal, Popconfirm, Row,
  Segmented, Space, Tag, Tooltip, Typography,
} from 'antd'
import {
  DeleteOutlined, EnvironmentOutlined, PlusOutlined, QrcodeOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import QrScanner from '../components/QrScanner'
import { BARCODE_FORMATS, scanCandidates } from '../lib/barcode'
import { api } from '../api/client'
import type { Row as ARow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

export default function LocatorPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const canWrite = !readOnly && (user?.level ?? 0) >= 1

  const [mode, setMode] = useState<'find' | 'racks' | 'contents'>('find')
  const [term, setTerm] = useState('')
  const [rackCode, setRackCode] = useState('')
  const [scanFor, setScanFor] = useState<null | 'material' | 'rack'>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [assignFor, setAssignFor] = useState<ARow | null>(null)

  const found = useQuery({
    queryKey: ['/locations/lookup', term],
    queryFn: () => api.get('/locations/lookup', { params: { q: term, limit: 50 } })
      .then((r) => r.data),
    enabled: mode === 'find' && term.trim().length >= 2,
  })
  const racks = useQuery({
    queryKey: ['/locations'],
    queryFn: () => api.get('/locations').then((r) => r.data),
  })
  const contents = useQuery({
    queryKey: ['/locations/contents', rackCode],
    queryFn: () => api.get(`/locations/${encodeURIComponent(rackCode)}/contents`)
      .then((r) => r.data),
    enabled: mode === 'contents' && rackCode.trim().length > 0,
    retry: false,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/locations'] })
    qc.invalidateQueries({ queryKey: ['/locations/lookup'] })
    qc.invalidateQueries({ queryKey: ['/locations/contents'] })
  }
  const createRack = useMutation({
    mutationFn: (body: ARow) => api.post('/locations', body).then((r) => r.data),
    onSuccess: () => { message.success('Rack created'); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      message.error(e?.response?.data?.detail ?? 'Could not create that rack'),
  })
  const assign = useMutation({
    mutationFn: (body: ARow) => api.put('/locations/material', body).then((r) => r.data),
    onSuccess: (d) => { message.success(`Assigned to ${d.location}`); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      message.error(e?.response?.data?.detail ?? 'Could not assign'),
  })
  const removeRack = useMutation({
    mutationFn: (id: number) => api.delete(`/locations/${id}`).then((r) => r.data),
    onSuccess: (d) => {
      message.success(`Rack deleted (${d.assignments_removed} assignment(s) removed)`)
      invalidate()
    },
  })

  // A scan is just another way of typing. `scanCandidates` already understands
  // the label shapes in the wild (`SAP|Description`, `SAP:1163`, URLs, JSON),
  // so the decoded string is normalised before it reaches a query.
  const onDecode = (text: string) => {
    const best = scanCandidates(text)[0] ?? text
    if (scanFor === 'rack') { setRackCode(best); setMode('contents') }
    else { setTerm(best); setMode('find') }
    setScanFor(null)
  }

  const findCols = [
    { title: 'SAP', dataIndex: 'SAP_Code', width: 110,
      render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Material', dataIndex: 'Material_Code', width: 140,
      render: (v: string) => <span style={mono}>{v || '—'}</span> },
    { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'Go to', dataIndex: 'primary_location', width: 260,
      render: (_: unknown, r: ARow) => {
        const locs = (r.locations ?? []) as ARow[]
        if (!locs.length) {
          return (
            <Tooltip title="We stock this, but nobody has recorded where it lives">
              <Tag>not located</Tag>
            </Tooltip>)
        }
        return (
          <Space size={4} wrap>
            {locs.map((l) => (
              <Tag key={String(l.assignment_id)} color={l.is_primary ? 'blue' : undefined}
                style={mono} icon={<EnvironmentOutlined />}>
                {String(l.code)}{l.label ? ` · ${l.label}` : ''}
              </Tag>))}
          </Space>)
      } },
    ...(canWrite ? [{
      title: '', key: 'act', width: 110,
      render: (_: unknown, r: ARow) => (
        <Button size="small" onClick={() => setAssignFor(r)}>Put in rack</Button>),
    }] : []),
  ]

  const rackCols = [
    { title: 'Code', dataIndex: 'code', width: 130,
      render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Where', dataIndex: 'label', ellipsis: true },
    { title: 'Zone', dataIndex: 'zone', width: 90 },
    { title: 'Rack', dataIndex: 'rack_no', width: 90 },
    { title: 'Row', dataIndex: 'row_no', width: 80 },
    { title: 'Bin', dataIndex: 'bin_no', width: 80 },
    { title: 'Status', dataIndex: 'status', width: 100,
      render: (v: string) => <Tag color={v === 'active' ? 'green' : undefined}>{v}</Tag> },
    ...(canWrite ? [{
      title: '', key: 'act', width: 130,
      render: (_: unknown, r: ARow) => (
        <Space>
          <Button size="small" onClick={() => { setRackCode(String(r.code)); setMode('contents') }}>
            Contents
          </Button>
          <Popconfirm title="Delete this rack? Its material assignments go too."
            onConfirm={() => removeRack.mutate(Number(r.id))}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>),
    }] : []),
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        <EnvironmentOutlined /> Warehouse Locator
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Search a material to be told the rack, manage the racks themselves, or
        scan a shelf's own QR to see what is meant to be on it.
      </Typography.Paragraph>

      <Segmented value={mode} onChange={(v) => setMode(v as typeof mode)}
        style={{ marginBottom: 12 }}
        options={[
          { value: 'find', label: 'Find a material' },
          { value: 'racks', label: 'Racks' },
          { value: 'contents', label: 'Scan a rack' },
        ]} />

      {mode === 'find' && (
        <Card size="small">
          <Space.Compact style={{ width: '100%', maxWidth: 640, marginBottom: 12 }}>
            <Input allowClear prefix={<SearchOutlined />} value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="SAP code, material code or description…" />
            <Button icon={<QrcodeOutlined />} onClick={() => setScanFor('material')}>
              Scan
            </Button>
          </Space.Compact>
          {term.trim().length < 2
            ? <Empty description="Type at least two characters, or scan a label" />
            : (
              <Table rowKey="SAP_Code" size="small" columns={findCols}
                dataSource={(found.data?.items ?? []) as ARow[]}
                loading={found.isFetching}
                pagination={{ pageSize: 20, showSizeChanger: true }} />)}
        </Card>)}

      {mode === 'racks' && (
        <Card size="small" extra={canWrite && (
          <Button type="primary" size="small" icon={<PlusOutlined />}
            onClick={() => setAddOpen(true)}>Add rack</Button>)}>
          <Table rowKey="id" size="small" columns={rackCols}
            dataSource={(racks.data?.items ?? []) as ARow[]}
            loading={racks.isFetching}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            locale={{ emptyText: <Empty description="No racks yet — add one to start mapping the warehouse" /> }} />
        </Card>)}

      {mode === 'contents' && (
        <Card size="small">
          <Space.Compact style={{ width: '100%', maxWidth: 480, marginBottom: 12 }}>
            <Input allowClear value={rackCode} onChange={(e) => setRackCode(e.target.value)}
              placeholder="Rack code, e.g. A-03-2" />
            <Button icon={<QrcodeOutlined />} onClick={() => setScanFor('rack')}>Scan</Button>
          </Space.Compact>
          {contents.isError && (
            <Alert type="warning" showIcon message={`No storage location "${rackCode}"`} />)}
          {contents.data && (
            <>
              <Alert type="info" showIcon style={{ marginBottom: 12 }}
                message={<span style={mono}>{String(contents.data.location.code)}</span>}
                description={String(contents.data.location.label ?? '')} />
              <Table rowKey="assignment_id" size="small"
                dataSource={(contents.data.items ?? []) as ARow[]}
                loading={contents.isFetching} pagination={false}
                columns={[
                  { title: 'SAP', dataIndex: 'SAP_Code', width: 110,
                    render: (v: string) => <b style={mono}>{v}</b> },
                  { title: 'Material', dataIndex: 'Material_Code', width: 140 },
                  { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
                  { title: 'UOM', dataIndex: 'UOM', width: 80 },
                  { title: 'Primary', dataIndex: 'is_primary', width: 100,
                    render: (v: boolean) => v ? <Tag color="blue">primary</Tag> : null },
                ]}
                locale={{ emptyText: <Empty description="This rack is empty" /> }} />
            </>)}
        </Card>)}

      <QrScanner open={!!scanFor} onClose={() => setScanFor(null)} onDecode={onDecode}
        formats={BARCODE_FORMATS}
        title={scanFor === 'rack' ? 'Scan a rack label' : 'Scan a material label'}
        manualPlaceholder={scanFor === 'rack' ? '…or type the rack code'
          : '…or type the SAP code'} />

      <Modal open={addOpen} onCancel={() => setAddOpen(false)} footer={null}
        title="Add a storage location" destroyOnHidden>
        <Form layout="vertical" onFinish={(v) => createRack.mutate(v, {
          onSuccess: () => setAddOpen(false) })}>
          <Form.Item name="code" label="Code" rules={[{ required: true }]}
            extra="This is what goes on the shelf's QR label — keep it short, e.g. A-03-2.">
            <Input placeholder="A-03-2" />
          </Form.Item>
          <Row gutter={8}>
            <Col span={6}><Form.Item name="zone" label="Zone"><Input placeholder="A" /></Form.Item></Col>
            <Col span={6}><Form.Item name="rack_no" label="Rack"><Input placeholder="03" /></Form.Item></Col>
            <Col span={6}><Form.Item name="row_no" label="Row"><Input placeholder="2" /></Form.Item></Col>
            <Col span={6}><Form.Item name="bin_no" label="Bin"><Input /></Form.Item></Col>
          </Row>
          <Form.Item name="description" label="Description"><Input /></Form.Item>
          <Button type="primary" htmlType="submit" loading={createRack.isPending}>
            Create
          </Button>
        </Form>
      </Modal>

      <Modal open={!!assignFor} onCancel={() => setAssignFor(null)} footer={null}
        title={`Put ${assignFor?.SAP_Code ?? ''} in a rack`} destroyOnHidden>
        <Form layout="vertical" onFinish={(v) => assign.mutate(
          { ...v, SAP_Code: assignFor?.SAP_Code, location_id: Number(v.location_id) },
          { onSuccess: () => setAssignFor(null) })}>
          <Form.Item name="location_id" label="Rack" rules={[{ required: true }]}>
            <Input list="gi-rack-list" placeholder="Pick a rack" />
          </Form.Item>
          <datalist id="gi-rack-list">
            {((racks.data?.items ?? []) as ARow[]).map((r) => (
              <option key={String(r.id)} value={String(r.id)}>
                {String(r.code)} — {String(r.label ?? '')}
              </option>))}
          </datalist>
          <Form.Item name="note" label="Note"><Input /></Form.Item>
          <Button type="primary" htmlType="submit" loading={assign.isPending}>
            Assign
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
