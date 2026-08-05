/**
 * frontend/src/pages/AssetsPage.tsx — serialised assets: which one, and where.
 *
 * THE TWO-HAMMERS SCREEN. Two hammers share a SAP code, so scanning either
 * label used to resolve to the same material card. Here a scan goes through
 * `/assets/resolve`, which answers with the exact unit when the sticker
 * carries a serial, and with a CHOICE when it only carries the SAP — the user
 * picks the one in their hand.
 *
 * SPEED IS THE POINT. From scan to "location saved" is two taps: scan → pick
 * (only when ambiguous) → Update location. The GPS is captured in the
 * background of that same tap and never gates it.
 *
 * ⚠️ GPS IS BEST-EFFORT. Permission declined, no fix indoors, or a page served
 * without HTTPS all still record the move — the coordinate is simply absent,
 * and the UI says which happened instead of failing. See lib/geolocation.ts.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Col, Descriptions, Empty, Form, Input, List, Modal,
  Row, Select, Space, Switch, Tag, Timeline, Tooltip, Typography,
} from 'antd'
import {
  AimOutlined, EnvironmentOutlined, PlusOutlined, QrcodeOutlined, SearchOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import QrScanner from '../components/QrScanner'
import { BARCODE_FORMATS, scanCandidates } from '../lib/barcode'
import {
  formatFix, geolocationBlockedReason, getPosition, mapsUrl,
} from '../lib/geolocation'
import { api } from '../api/client'
import type { Row as ARow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

const STATUS_COLOR: Record<string, string> = {
  in_stock: 'green', issued: 'blue', returned: 'cyan',
  lost: 'red', scrapped: 'default',
  working: 'green', not_in_use: 'gold', repair: 'orange',
}

/** The status picker, grouped by what the value actually says.
 *
 * Mirrors `assets._STATUS_VALUES` — the API rejects anything else with a 422,
 * so a value added on one side and not the other fails loudly rather than
 * writing a status nothing renders.
 *
 * CONDITION comes first because it is what somebody standing in front of the
 * hammer knows: the workbook has no Status column, so this picker is where the
 * condition gets recorded at all. */
const STATUS_GROUPS = [
  { label: 'Condition', options: [
    { value: 'working', label: 'working' },
    { value: 'not_in_use', label: 'not in use' },
    { value: 'repair', label: 'under repair' },
  ] },
  { label: 'Custody', options: [
    { value: 'in_stock', label: 'in stock' },
    { value: 'issued', label: 'issued' },
    { value: 'returned', label: 'returned' },
    { value: 'lost', label: 'lost' },
    { value: 'scrapped', label: 'scrapped' },
  ] },
]

export default function AssetsPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const canWrite = !readOnly && (user?.level ?? 0) >= 1
  const gpsBlocked = geolocationBlockedReason()

  const [term, setTerm] = useState('')
  const [scanOpen, setScanOpen] = useState(false)
  const [choice, setChoice] = useState<ARow[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [moveFor, setMoveFor] = useState<ARow | null>(null)
  const [useGps, setUseGps] = useState(true)

  const units = useQuery({
    queryKey: ['/assets', term],
    queryFn: () => api.get('/assets', { params: term ? { q: term } : {} })
      .then((r) => r.data),
  })
  const racks = useQuery({
    queryKey: ['/locations'],
    queryFn: () => api.get('/locations').then((r) => r.data),
  })
  const detail = useQuery({
    queryKey: ['/assets', openId],
    queryFn: () => api.get(`/assets/${openId}`).then((r) => r.data),
    enabled: openId != null,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['/assets'] })

  const register = useMutation({
    mutationFn: (b: ARow) => api.post('/assets', b).then((r) => r.data),
    onSuccess: () => { message.success('Unit registered'); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      message.error(e?.response?.data?.detail ?? 'Could not register that unit'),
  })
  const move = useMutation({
    mutationFn: ({ id, body }: { id: number; body: ARow }) =>
      api.patch(`/assets/${id}/location`, body).then((r) => r.data),
    onSuccess: (d) => {
      message.success(d.gps_recorded
        ? 'Location saved, with coordinates'
        : 'Location saved (no coordinates)')
      invalidate()
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      message.error(e?.response?.data?.detail ?? 'Could not save that location'),
  })

  // A scan resolves to a unit, a choice, or an unregistered material — the
  // three cases the API distinguishes, surfaced without collapsing any of them.
  const onDecode = async (text: string) => {
    setScanOpen(false)
    const best = scanCandidates(text)[0] ?? text
    try {
      const { data } = await api.get('/assets/resolve', { params: { scan: best } })
      if (data.kind === 'unit') { setOpenId(Number(data.unit.id)); return }
      if (data.kind === 'choice') {
        setChoice(data.units as ARow[])
        if (data.reason === 'serial_ambiguous') {
          message.warning('That serial exists on more than one material — pick one')
        }
        return
      }
      setTerm(best)
      message.info(`No serialised unit for ${best} — showing a search instead`)
    } catch {
      setTerm(best)
    }
  }

  const doMove = async (values: ARow) => {
    if (!moveFor) return
    const body: ARow = {
      location_id: values.location_id ? Number(values.location_id) : null,
      location_note: values.location_note ?? null,
      holder: values.holder ?? null,
      status: values.status ?? null,
      note: values.note ?? null,
      source: 'manual',
    }
    // Captured alongside the move, never in front of it: a declined permission
    // or a warehouse with no signal must not stop the update from landing.
    if (useGps && !gpsBlocked) {
      const pos = await getPosition()
      if (pos.ok) body.gps = pos.fix
      else message.info(pos.reason)
    }
    move.mutate({ id: Number(moveFor.id), body }, { onSuccess: () => setMoveFor(null) })
  }

  const cols = [
    { title: 'SAP', dataIndex: 'SAP_Code', width: 110,
      render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Serial', dataIndex: 'serial_no', width: 150,
      render: (v: string) => <span style={mono}>{v}</span> },
    { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'Status', dataIndex: 'status', width: 110,
      render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
    { title: 'Where', dataIndex: 'where', width: 190,
      render: (v: string | null) => v
        ? <Tag icon={<EnvironmentOutlined />} style={mono}>{v}</Tag>
        : <span style={{ opacity: 0.4 }}>unknown</span> },
    { title: 'Held by', dataIndex: 'holder', width: 140,
      render: (v: string | null) => v || <span style={{ opacity: 0.4 }}>—</span> },
    { title: 'Map', dataIndex: 'maps_url', width: 80,
      render: (v: string | null) => v
        ? <a href={v} target="_blank" rel="noreferrer">open</a>
        : <span style={{ opacity: 0.4 }}>—</span> },
    { title: '', key: 'act', width: 190,
      render: (_: unknown, r: ARow) => (
        <Space>
          <Button size="small" onClick={() => setOpenId(Number(r.id))}>History</Button>
          {canWrite && (
            <Button size="small" type="primary" ghost onClick={() => setMoveFor(r)}>
              Update location
            </Button>)}
        </Space>) },
  ]

  const rackOptions = ((racks.data?.items ?? []) as ARow[]).map((r) => ({
    value: String(r.id),
    label: `${String(r.code)}${r.label ? ` — ${String(r.label)}` : ''}`,
  }))

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        <AimOutlined /> Asset Tracking
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Serialised items — tools, equipment, anything where <i>which one</i>
        {' '}matters. Scan a label to jump straight to that unit; when several
        share a SAP code you pick the one in your hand.
      </Typography.Paragraph>

      {gpsBlocked && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="Coordinates cannot be captured on this page"
          description={gpsBlocked} />)}

      <Card size="small" style={{ marginBottom: 12 }}
        extra={canWrite && (
          <Button type="primary" size="small" icon={<PlusOutlined />}
            onClick={() => setAddOpen(true)}>Register unit</Button>)}>
        <Space.Compact style={{ width: '100%', maxWidth: 620 }}>
          <Input allowClear prefix={<SearchOutlined />} value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Serial, asset tag, SAP code or holder…" />
          <Button icon={<QrcodeOutlined />} onClick={() => setScanOpen(true)}>
            Scan
          </Button>
        </Space.Compact>
      </Card>

      <Table rowKey="id" size="small" columns={cols}
        dataSource={(units.data?.items ?? []) as ARow[]}
        loading={units.isFetching}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{ emptyText: (
          <Empty description="No serialised units yet — register one to start tracking it" />) }} />

      <QrScanner open={scanOpen} onClose={() => setScanOpen(false)} onDecode={onDecode}
        formats={BARCODE_FORMATS} title="Scan an asset label"
        manualPlaceholder="…or type the serial / SAP code" />

      {/* The two-hammers choice. */}
      <Modal open={!!choice} onCancel={() => setChoice(null)} footer={null}
        title="Which one is in your hand?" destroyOnHidden>
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          That label names a material with more than one unit. Pick the serial
          printed on the item itself.
        </Typography.Paragraph>
        <List dataSource={choice ?? []} rowKey={(r) => String(r.id)}
          renderItem={(r) => (
            <List.Item actions={[
              <Button key="o" size="small" onClick={() => {
                setOpenId(Number(r.id)); setChoice(null)
              }}>Open</Button>,
              ...(canWrite ? [
                <Button key="m" size="small" type="primary" ghost onClick={() => {
                  setMoveFor(r); setChoice(null)
                }}>Update location</Button>] : []),
            ]}>
              <List.Item.Meta
                title={<span style={mono}>{String(r.serial_no)}</span>}
                description={
                  <Space size={4} wrap>
                    <Tag color={STATUS_COLOR[String(r.status)] ?? 'default'}>
                      {String(r.status)}
                    </Tag>
                    {r.where
                      ? <Tag icon={<EnvironmentOutlined />}>{String(r.where)}</Tag>
                      : <Tag>location unknown</Tag>}
                    {r.holder ? <span>held by {String(r.holder)}</span> : null}
                  </Space>} />
            </List.Item>)} />
      </Modal>

      {/* One unit + where it has been. */}
      <Modal open={openId != null} onCancel={() => setOpenId(null)} footer={null}
        width={720} title="Asset unit" destroyOnHidden>
        {detail.data && (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
              <Descriptions.Item label="SAP">
                <span style={mono}>{String(detail.data.unit.SAP_Code)}</span>
              </Descriptions.Item>
              <Descriptions.Item label="Serial">
                <span style={mono}>{String(detail.data.unit.serial_no)}</span>
              </Descriptions.Item>
              <Descriptions.Item label="Description" span={2}>
                {String(detail.data.unit.Equipment_Description ?? '—')}
              </Descriptions.Item>
              <Descriptions.Item label="Status">
                <Tag color={STATUS_COLOR[String(detail.data.unit.status)] ?? 'default'}>
                  {String(detail.data.unit.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Where">
                {String(detail.data.unit.where ?? 'unknown')}
              </Descriptions.Item>
              <Descriptions.Item label="Coordinates" span={2}>
                {formatFix(detail.data.unit.current_lat as number,
                  detail.data.unit.current_lng as number,
                  detail.data.unit.gps_accuracy_m as number)}
                {detail.data.unit.current_lat != null && (
                  <> · <a target="_blank" rel="noreferrer"
                    href={mapsUrl(Number(detail.data.unit.current_lat),
                      Number(detail.data.unit.current_lng))}>open in Maps</a></>)}
              </Descriptions.Item>
            </Descriptions>
            <Typography.Text strong>Movement history</Typography.Text>
            <Timeline style={{ marginTop: 12 }} items={
              (detail.data.movements as ARow[]).map((m) => ({
                children: (
                  <div>
                    <b>{String(m.to_note || m.to_location_id || '—')}</b>
                    {' · '}<span style={{ opacity: 0.7 }}>{String(m.source ?? '')}</span>
                    <div style={{ fontSize: 12, opacity: 0.65 }}>
                      {String(m.moved_at ?? '').slice(0, 19).replace('T', ' ')}
                      {m.moved_by ? ` · ${String(m.moved_by)}` : ''}
                      {m.lat != null ? ` · ${formatFix(m.lat as number, m.lng as number,
                        m.accuracy_m as number)}` : ' · no coordinates'}
                      {m.maps_url ? <> · <a href={String(m.maps_url)} target="_blank"
                        rel="noreferrer">map</a></> : null}
                    </div>
                    {m.note ? <div style={{ fontSize: 12 }}>{String(m.note)}</div> : null}
                  </div>),
              }))} />
          </>)}
      </Modal>

      {/* Register. */}
      <Modal open={addOpen} onCancel={() => setAddOpen(false)} footer={null}
        title="Register a physical unit" destroyOnHidden>
        <Form layout="vertical" onFinish={(v) => register.mutate(
          { ...v, current_location_id: v.current_location_id
            ? Number(v.current_location_id) : undefined },
          { onSuccess: () => setAddOpen(false) })}>
          <Row gutter={8}>
            <Col span={12}>
              <Form.Item name="SAP_Code" label="SAP code" rules={[{ required: true }]}>
                <Input placeholder="1163" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="serial_no" label="Serial number" rules={[{ required: true }]}
                extra="The unique number on the item itself.">
                <Input placeholder="HMR-0007" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="asset_tag" label="Asset tag (if the QR differs from the serial)">
            <Input />
          </Form.Item>
          <Form.Item name="current_location_id" label="Rack">
            <Select allowClear showSearch optionFilterProp="label" options={rackOptions} />
          </Form.Item>
          <Form.Item name="location_note" label="…or describe where it is">
            <Input placeholder="With the subcontractor, Bay 4" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="status" label="Status"
                extra="The workbook has no Status column — this is where the
                       condition gets recorded.">
                <Select allowClear placeholder="working / in stock / …"
                  options={STATUS_GROUPS} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="holder" label="Held by"><Input /></Form.Item>
            </Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={register.isPending}>
            Register
          </Button>
        </Form>
      </Modal>

      {/* Move — the two-tap path. */}
      <Modal open={!!moveFor} onCancel={() => setMoveFor(null)} footer={null}
        title={moveFor ? `Update location — ${moveFor.serial_no}` : ''} destroyOnHidden>
        {moveFor && (
          <Form layout="vertical"
            initialValues={{
              location_id: moveFor.current_location_id
                ? String(moveFor.current_location_id) : undefined,
              location_note: moveFor.location_note ?? undefined,
              holder: moveFor.holder ?? undefined,
              status: moveFor.status,
            }}
            onFinish={doMove}>
            <Form.Item name="location_id" label="Rack">
              <Select allowClear showSearch optionFilterProp="label" options={rackOptions}
                placeholder="Pick a rack" />
            </Form.Item>
            <Form.Item name="location_note" label="…or describe the place"
              extra="Not everything lives on a shelf — a yard, a vehicle, a subcontractor.">
              <Input placeholder="Loaded on truck 4771" />
            </Form.Item>
            <Row gutter={8}>
              <Col span={12}>
                <Form.Item name="status" label="Status">
                  <Select options={STATUS_GROUPS} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="holder" label="Held by"><Input /></Form.Item>
              </Col>
            </Row>
            <Form.Item name="note" label="Note"><Input /></Form.Item>
            <Form.Item label={
              <Space>
                <span>Capture coordinates</span>
                <Tooltip title="Best-effort. If the permission is declined or there is
                  no signal, the location is still saved — only the coordinates are
                  missing.">
                  <Typography.Text type="secondary">(optional)</Typography.Text>
                </Tooltip>
              </Space>}>
              <Switch checked={useGps && !gpsBlocked} disabled={!!gpsBlocked}
                onChange={setUseGps} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={move.isPending}>
              Save location
            </Button>
          </Form>)}
      </Modal>
    </div>
  )
}
