import { Card, Col, Empty, Row, Typography } from 'antd'
import { Table } from '../lib/smartTable'
import { DollarOutlined, EnvironmentOutlined, InboxOutlined, WarningOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { useDashboardMetrics, useInventorySummary, useList, useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import AskDataCard from '../components/AskDataCard'
import BrowseTable from '../components/BrowseTable'
import KpiCard from '../components/KpiCard'
import { brand, status } from '../theme/tokens'

interface CatRow {
  Category: string | null
  count: number
}

const nf = (v: number) => v.toLocaleString('en-US')

/** Share of the master this category holds — the counts alone don't say whether
 *  "Chemicals: 84" is most of the inventory or a rounding error. */
function catColumnsFor(total: number): ColumnsType<CatRow> {
  return [
    { title: 'Category', dataIndex: 'Category', key: 'Category', render: (v) => v ?? '—' },
    { title: 'Items', dataIndex: 'count', key: 'count', align: 'right', width: 90 },
    {
      title: 'Share', dataIndex: 'count', key: 'share', align: 'right', width: 90,
      render: (v: number) => (
        <Typography.Text type="secondary" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {total > 0 ? `${((v / total) * 100).toFixed(1)}%` : '—'}
        </Typography.Text>
      ),
    },
  ]
}

export default function Dashboard() {
  const { data: summary } = useInventorySummary()
  const { data: sites } = useSites()
  const { user } = useAuth()
  const expiring = useList('/stock/expiring', { limit: 1 })
  const expiringCount = expiring.data?.total ?? 0
  const { data: metrics } = useDashboardMetrics()

  const byCategory = summary?.by_category ?? []
  const catTotal = byCategory.reduce((s, r) => s + (r.count ?? 0), 0)
  const catColumns = catColumnsFor(catTotal)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Dashboard
      </Typography.Title>

      {/* Four cards, one clean row. The fifth used to be a "Database: online"
          card stranded on a row of its own — it read the same `useHealth()` as
          the header's "API online" chip, which is on every page, not just this
          one. A KPI slot is worth more than a duplicated status light. */}
      <Row gutter={[16, 16]} className="gi-cascade">
        <Col xs={12} md={6}>
          <KpiCard
            title="Inventory items"
            value={summary?.total_items ?? 0}
            icon={<InboxOutlined />}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Stock value (SAR)"
            value={metrics ? Math.round(metrics.valuation_total).toLocaleString() : '—'}
            icon={<DollarOutlined />}
            tint={brand.gold}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Sites"
            value={sites?.length ?? 0}
            icon={<EnvironmentOutlined />}
            tint={status.info}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Expiring / expired lots"
            value={expiringCount}
            icon={<WarningOutlined />}
            tint={expiringCount > 0 ? status.critical : status.ok}
            valueColor={expiringCount > 0 ? status.critical : undefined}
          />
        </Col>
      </Row>

      {/* Phase 5 — legacy visual parity: stock-vs-min, burn forecast, top-consumed. */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={8}>
          <Card title="Stock vs Minimum (lowest coverage)" size="small">
            {metrics?.stock_vs_min?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.stock_vs_min} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Legend />
                  <Bar dataKey="current" name="Current" fill={status.info} />
                  <Bar dataKey="minimum" name="Minimum" fill={status.critical} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No minimums set" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Burn forecast — days of cover" size="small">
            {metrics?.burn_forecast?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.burn_forecast} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Bar dataKey="days_remaining" name="Days of cover" fill={brand.gold} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No consumption in 30 days" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Top consumed (30 days)" size="small">
            {metrics?.top_consumed?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.top_consumed} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Bar dataKey="consumed" name="Consumed" fill={status.ok} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No consumption in 30 days" />}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={10}>
          <Card title="Inventory by category" size="small">
            <Table<CatRow> sticky={{ offsetHeader: 64 }}
              size="small"
              columns={catColumns}
              dataSource={byCategory}
              rowKey={(r) => String(r.Category)}
              pagination={false}
              scroll={{ y: 320 }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title="Expiring & expired stock" size="small">
            <BrowseTable path="/stock/expiring" hasSite />
          </Card>
        </Col>
      </Row>

      {/* Phase C — chat-with-your-data: template lane serves HODs (site-pinned
          server-side); the NL→SQL lane still backs unscoped roles. */}
      {(user?.level ?? 0) >= 2 && <AskDataCard />}
    </div>
  )
}
