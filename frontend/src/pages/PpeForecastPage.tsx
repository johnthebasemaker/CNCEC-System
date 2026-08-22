/**
 * PpeForecastPage — what to buy in the next 15 days, and whose gear it is.
 *
 * Deterministic, not predictive (ruling R5): expiring − on hand − already on
 * order. There are 22 roster workers and no distribution history to fit a
 * model to, and a confidently-wrong forecast is worse than an arithmetic one.
 * The page says so out loud rather than implying a cleverness it does not have.
 *
 * Every row expands to the PEOPLE behind the number. That is not decoration:
 * a list of quantities cannot be sanity-checked by a human, a list of names
 * can, and the SK needs to know whose boots to go and look at.
 *
 * "Expired" here is a SUGGESTED REPLACEMENT DATE, never a restriction. Nobody
 * is stopped from working and no issue is blocked by anything on this page.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Empty, Segmented, Space, Statistic, Tag, Tooltip,
  Typography,
} from 'antd'
import { DownloadOutlined, FieldTimeOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import KpiRow from '../components/KpiRow'
import { Table } from '../lib/smartTable'
import { downloadPpe, usePpeForecast, useSites } from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'

const num = (v: unknown) => Number(v ?? 0)

export default function PpeForecastPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const scoped = (user?.level ?? 0) < 3
  const [site, setSite] = useState<string | undefined>(
    scoped ? (user?.site_id || undefined) : undefined)
  const [days, setDays] = useState(15)
  const { data, isLoading } = usePpeForecast(site, days)

  const items = (data?.items as Row[] | undefined) ?? []
  const toOrder = items.filter((i) => num(i.suggested_order_qty) > 0)

  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'SAP_Code', key: 'sap', width: 110 },
    { title: 'Description', dataIndex: 'Description', key: 'd', ellipsis: true },
    {
      title: 'Expiring', dataIndex: 'expiring_qty', key: 'exp', align: 'right',
      width: 100, render: num,
    },
    {
      title: 'People', dataIndex: 'people_count', key: 'pc', align: 'right',
      width: 90,
      render: (v, r) => (
        <span>
          {num(v)}
          {num(r.overdue_count) > 0 && (
            <Tag color="red" style={{ marginLeft: 6 }}>{num(r.overdue_count)} due</Tag>
          )}
        </span>
      ),
    },
    { title: 'On hand', dataIndex: 'on_hand', key: 'oh', align: 'right',
      width: 100, render: num },
    {
      title: 'On order', dataIndex: 'on_order', key: 'oo', align: 'right', width: 100,
      render: (v) => (
        <Tooltip title="Open purchase-order lines not yet delivered. Netted off so nothing is ordered twice.">
          <span>{num(v)}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Order', dataIndex: 'suggested_order_qty', key: 'sug', align: 'right',
      width: 110,
      render: (v) => (num(v) > 0
        ? <Tag color="gold" style={{ fontWeight: 600 }}>{num(v)}</Tag>
        : <Typography.Text type="secondary">covered</Typography.Text>),
    },
    { title: 'Earliest', dataIndex: 'earliest_expiry', key: 'ee', width: 110 },
  ]

  const peopleCols: ColumnsType<Row> = [
    { title: 'Employee', dataIndex: 'employee_name', key: 'n' },
    { title: 'ID', dataIndex: 'employee_id_number', key: 'id', width: 120 },
    { title: 'Site', dataIndex: 'Site_ID', key: 's', width: 110 },
    { title: 'Qty', dataIndex: 'Qty', key: 'q', align: 'right', width: 80, render: num },
    { title: 'Issued', dataIndex: 'issued_on', key: 'i', width: 110 },
    {
      title: 'Replace by', dataIndex: 'expires_on', key: 'e', width: 150,
      render: (v, r) => (r.overdue
        ? <Tag color="red">{String(v)} · {Math.abs(num(r.days_left))}d ago</Tag>
        : <Tag color="gold">{String(v)} · in {num(r.days_left)}d</Tag>),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <FieldTimeOutlined /> PPE Order Forecast
        </Typography.Title>
        <Typography.Text type="secondary">
          Protective equipment reaching its replacement date in the next{' '}
          {days} days, netted against what is on the shelf and what is already
          on order — so a bulk order can be raised once instead of item by item.
        </Typography.Text>
      </div>

      <Space wrap>
        <Segmented
          value={days} onChange={(v) => setDays(Number(v))}
          options={[{ label: '15 days', value: 15 }, { label: '30 days', value: 30 },
                    { label: '60 days', value: 60 }, { label: '90 days', value: 90 }]}
        />
        {!scoped && (
          <Segmented
            value={site ?? '__all'}
            onChange={(v) => setSite(v === '__all' ? undefined : String(v))}
            options={[{ label: 'All sites', value: '__all' },
                      ...(sites ?? []).map((s) => ({ label: s, value: s }))]}
          />
        )}
        <Button icon={<DownloadOutlined />} onClick={async () => {
          try {
            await downloadPpe('forecast', 'xlsx', { site_id: site, days })
          } catch { message.error('Could not download the forecast') }
        }}>Export</Button>
      </Space>

      {/* `Space wrap` sized each card to its own content, so three KPIs
          huddled on the left of a 1,280px page. */}
      <KpiRow>
        <Card size="small"><Statistic title="Items to order" value={toOrder.length} /></Card>
        <Card size="small">
          <Statistic title="Total units suggested" value={num(data?.total_suggested)} />
        </Card>
        <Card size="small">
          <Statistic title="People affected" value={num(data?.total_people)} />
        </Card>
      </KpiRow>

      <Alert
        type="info" showIcon
        title="This is arithmetic, not a prediction"
        description="Expiring minus what is on the shelf minus what is already on an open purchase order. A replacement date is a suggestion — nobody is stopped from working, and no issue is blocked by anything on this page."
      />

      <Card size="small" title={`Expiring by ${String(data?.horizon ?? '')}`}>
        <Table
          sticky={{ offsetHeader: 64 }} size="small" loading={isLoading}
          columns={columns} dataSource={items}
          rowKey={(r) => String(r.SAP_Code)} scroll={{ x: 'max-content' }}
          expandable={{
            // The names are the point of the page, so the row opens to them
            // rather than hiding them behind a second navigation.
            expandedRowRender: (r) => (
              <Table
                size="small" columns={peopleCols}
                dataSource={(r.people as Row[]) ?? []}
                rowKey={(p) => `${r.SAP_Code}-${p.employee_id_number}-${p.issued_on}`}
                pagination={false}
              />
            ),
            rowExpandable: (r) => ((r.people as Row[]) ?? []).length > 0,
          }}
          locale={{
            emptyText: <Empty description={
              `No PPE is due for replacement in the next ${days} days.`} />,
          }}
        />
      </Card>
    </Space>
  )
}
