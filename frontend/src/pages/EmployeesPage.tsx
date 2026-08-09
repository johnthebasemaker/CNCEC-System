/**
 * EmployeesPage — the roster, a transfer, and one person's whole story.
 *
 * Serves two roles from one page. An HOD searches their own site, moves
 * somebody to another site, and sees what PPE travels with them. Admins and
 * logistics search globally and get the tracking timeline the requirement
 * asked for — current site plus a chart of every site the person has worked
 * at, which is the same drawer, opened from a global list instead of a
 * site-scoped one.
 *
 * The timeline is drawn from the server's `segments` array rather than from
 * raw movements: the API closes each segment ("at CNCEC from X to Y") and
 * synthesises an opening one for the period before the movements table
 * existed, so a worker hired last year does not render as a chart with no
 * beginning. Four callers each re-deriving "from when to when" is four
 * chances to get it different.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Descriptions, Drawer, Empty, Form, Input, Modal,
  Select, Space, Statistic, Tag, Timeline, Typography,
} from 'antd'
import { DownloadOutlined, SwapOutlined, TeamOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import {
  downloadPpe, useEmployeeTimeline, useHrDataQuality, useHrEmployees, useSites,
  useTransferEmployee,
} from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const num = (v: unknown) => Number(v ?? 0)

// One palette across the timeline and the site tags, so "CNCEC" is the same
// colour in the chart as it is in the table beside it.
const SITE_COLORS = ['blue', 'green', 'purple', 'magenta', 'cyan', 'orange']
const siteColor = (site: string, all: string[]) =>
  SITE_COLORS[Math.max(0, all.indexOf(site)) % SITE_COLORS.length]

function TransferModal({ employee, onClose }:
{ employee: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const transfer = useTransferEmployee()
  const { data: sites } = useSites()
  const [form] = Form.useForm()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const res = await transfer.mutateAsync({
        id_number: String(employee!.ID_Number), to_site: v.to_site,
        reason: v.reason,
      })
      message.success(
        `${employee!.Name} moved to ${res.to_site}`
        + (num(res.ppe_carried_over) > 0
          ? ` — ${num(res.ppe_carried_over)} PPE item(s) travel with them, and the receiving store keeper has been told`
          : ''))
      form.resetFields()
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal open={!!employee} onCancel={onClose} onOk={submit}
           title={`Transfer ${employee?.Name ?? ''}`} okText="Transfer"
           confirmLoading={transfer.isPending}>
      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        title="Their PPE history goes with them"
        description="It is recorded against the person, not the site, so the receiving store keeper sees what they already hold and does not issue it twice. Their timesheets stay where they were worked."
      />
      <Form form={form} layout="vertical">
        <Form.Item name="to_site" label="Move to site"
                   rules={[{ required: true, message: 'pick a site' }]}>
          <Select showSearch
                  options={(sites ?? [])
                    .filter((s) => s !== employee?.Site_ID)
                    .map((s) => ({ value: s, label: s }))} />
        </Form.Item>
        <Form.Item name="reason" label="Reason">
          <Input.TextArea rows={2} placeholder="Shutdown cover, reassignment…" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function PersonDrawer({ idNumber, onClose }:
{ idNumber: string | null; onClose: () => void }) {
  const { message } = App.useApp()
  const { data, isLoading } = useEmployeeTimeline(idNumber ?? undefined)
  const emp = data?.employee as Row | undefined
  const segments = (data?.segments as Row[] | undefined) ?? []
  const ppe = (data?.ppe as Row[] | undefined) ?? []
  const worked = (data?.worked_at as Row[] | undefined) ?? []
  const allSites = [...new Set(segments.map((s) => String(s.site)))]

  const ppeCols: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'SAP_Code', key: 's', width: 100 },
    { title: 'Description', dataIndex: 'Description', key: 'd', ellipsis: true },
    { title: 'Issued at', dataIndex: 'Site_ID', key: 'st', width: 110,
      render: (v) => <Tag color={siteColor(String(v), allSites)}>{String(v)}</Tag> },
    { title: 'Issued', dataIndex: 'issued_on', key: 'i', width: 110 },
    {
      title: 'Replace by', dataIndex: 'expires_on', key: 'e', width: 150,
      render: (v, r) => {
        if (!v) return <Typography.Text type="secondary">no rule set</Typography.Text>
        if (r.status !== 'active') return String(v)
        return r.overdue
          ? <Tag color="red">{String(v)} · due</Tag>
          : <Tag color="green">{String(v)} · in {num(r.days_left)}d</Tag>
      },
    },
    {
      title: 'Status', dataIndex: 'status', key: 'st2', width: 110,
      render: (v, r) => (
        <Space size={4}>
          <Tag color={v === 'active' ? 'green' : 'default'}>{String(v)}</Tag>
          {r.early_replacement ? <Tag color="orange">early</Tag> : null}
        </Space>
      ),
    },
    { title: 'Reason', dataIndex: 'early_reason', key: 'r', ellipsis: true,
      render: (v) => String(v ?? '') },
  ]

  return (
    <Drawer open={!!idNumber} onClose={onClose} size="large"
            title={emp ? `${emp.Name} · ${emp.ID_Number}` : 'Employee'}
            extra={idNumber && (
              <Button icon={<DownloadOutlined />} onClick={async () => {
                try { await downloadPpe('history', 'xlsx', { id_number: idNumber }) }
                catch { message.error('Could not download the history') }
              }}>PPE history</Button>
            )}>
      {isLoading && <Typography.Text type="secondary">Loading…</Typography.Text>}
      {emp && (
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Current site">
              <Tag color={siteColor(String(emp.Site_ID), allSites)}>
                {String(emp.Site_ID || '—')}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Status">{String(emp.status)}</Descriptions.Item>
            <Descriptions.Item label="Designation">
              {String(emp.Designation ?? '—')}
            </Descriptions.Item>
            <Descriptions.Item label="Company">
              {String(emp.Company ?? '—')}
            </Descriptions.Item>
          </Descriptions>

          <Space wrap size="large">
            <Card size="small">
              <Statistic title="Sites worked at" value={allSites.length} />
            </Card>
            <Card size="small">
              <Statistic title="PPE currently held"
                         value={ppe.filter((p) => p.status === 'active').length} />
            </Card>
            <Card size="small">
              <Statistic title="Days on timesheets"
                         value={worked.reduce((a, w) => a + num(w.days), 0)} />
            </Card>
          </Space>

          <Card size="small" title="Where they have worked">
            {segments.length === 0
              ? <Empty description="No site history recorded" />
              : (
                <Timeline
                  items={segments.map((s) => ({
                    color: siteColor(String(s.site), allSites),
                    children: (
                      <Space orientation="vertical" size={0}>
                        <Typography.Text strong>{String(s.site)}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {String(s.from ?? '—')} → {s.to ? String(s.to) : 'present'}
                          {s.origin === 'opening' ? ' (from their record opening)' : ''}
                        </Typography.Text>
                        {s.reason ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {String(s.reason)} · moved by {String(s.moved_by)}
                          </Typography.Text>
                        ) : null}
                      </Space>
                    ),
                  }))}
                />
              )}
          </Card>

          <Card size="small" title="PPE history (every site)">
            <Table size="small" columns={ppeCols} dataSource={ppe}
                   rowKey={(r) => String(r.id)} pagination={false}
                   scroll={{ x: 'max-content' }}
                   locale={{ emptyText: <Empty description="No PPE has been issued to this person" /> }} />
          </Card>

          {worked.length > 0 && (
            <Card size="small" title="Hours by site">
              <Table
                size="small" pagination={false} dataSource={worked}
                rowKey={(w) => String(w.site)}
                columns={[
                  { title: 'Site', dataIndex: 'site',
                    render: (v) => <Tag color={siteColor(String(v), allSites)}>{String(v)}</Tag> },
                  { title: 'First day', dataIndex: 'first_day' },
                  { title: 'Last day', dataIndex: 'last_day' },
                  { title: 'Days', dataIndex: 'days', align: 'right', render: num },
                  { title: 'Hours', dataIndex: 'hours', align: 'right', render: num },
                ]}
              />
            </Card>
          )}
        </Space>
      )}
    </Drawer>
  )
}

export default function EmployeesPage() {
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const [q, setQ] = useState('')
  const [site, setSite] = useState<string | undefined>()
  const { data: sites } = useSites()
  const { data: rows, isLoading } = useHrEmployees({ site_id: site, q: q || undefined })
  const { data: dq } = useHrDataQuality()
  const [transferring, setTransferring] = useState<Row | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)

  const isHod = user?.role === 'hod'
  const global = (user?.level ?? 0) >= 3
  const siteless = (dq?.siteless_employees as Row[] | undefined) ?? []

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'ID_Number', key: 'id', width: 120 },
    { title: 'Name', dataIndex: 'Name', key: 'n' },
    {
      title: 'Site', dataIndex: 'Site_ID', key: 's', width: 120,
      render: (v) => (v ? <Tag color="blue">{String(v)}</Tag>
                        : <Tag color="red">none — unusable</Tag>),
    },
    { title: 'Designation', dataIndex: 'Designation', key: 'd',
      render: (v) => String(v ?? '—') },
    { title: 'Company', dataIndex: 'Company', key: 'c',
      render: (v) => String(v ?? '—') },
    { title: 'Status', dataIndex: 'status', key: 'st', width: 100 },
    {
      title: '', key: '__act', width: 200, align: 'right',
      render: (_, r) => (
        <Space size="small">
          <Button size="small" onClick={() => setViewing(String(r.ID_Number))}>
            History
          </Button>
          <Button size="small" icon={<SwapOutlined />}
                  disabled={!isHod || readOnly}
                  onClick={() => setTransferring(r)}>
            Transfer
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <TeamOutlined /> Employees
        </Typography.Title>
        <Typography.Text type="secondary">
          One record per person. Their PPE history and their site history follow
          them wherever they are transferred; their timesheets stay at the site
          where the hours were actually worked.
        </Typography.Text>
      </div>

      {siteless.length > 0 && (
        <Alert
          type="warning" showIcon
          title={`${siteless.length} employee(s) have no site assigned`}
          description={
            <span>
              An employee with no site cannot be named on any supervisor
              material request — the check compares their site to the
              requesting site and a blank never matches. Assign one:{' '}
              {siteless.slice(0, 10).map((e) => (
                <Tag key={String(e.ID_Number)}>{String(e.ID_Number)} {String(e.Name)}</Tag>
              ))}
            </span>
          }
        />
      )}

      <Card
        size="small"
        title={
          <Space wrap>
            <Input.Search allowClear placeholder="Search by ID or name"
                          style={{ width: 260 }} onSearch={setQ} />
            {global && (
              <Select allowClear placeholder="All sites" style={{ width: 180 }}
                      value={site} onChange={setSite}
                      options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            )}
          </Space>
        }
      >
        <Table
          sticky={{ offsetHeader: 64 }} size="small" loading={isLoading}
          columns={columns} dataSource={rows ?? []}
          rowKey={(r) => String(r.ID_Number)} scroll={{ x: 'max-content' }}
        />
      </Card>

      <TransferModal employee={transferring} onClose={() => setTransferring(null)} />
      <PersonDrawer idNumber={viewing} onClose={() => setViewing(null)} />
    </Space>
  )
}
