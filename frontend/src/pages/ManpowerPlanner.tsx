/**
 * Phase 7 — the manpower planner.
 *
 * Three questions, in the order a planner actually asks them:
 *   1. WORKLOAD    — how much is left, and how many man-hours is that?
 *   2. GAP         — per role, what do we have against what we need?
 *   3. STRATEGY    — where does overtime come from, and how do we remove it?
 *
 * ⚠️ IT MUTATES NOTHING. This is advice: the operator's ruling is that the
 * planner suggests, never assigns. Nothing here writes to the roster.
 *
 * ⚠️ "Prefer non-GI" is arithmetic, not a policy about who to employ. Overtime
 * is whatever will not fit inside NORMAL capacity, and a non-GI worker brings
 * 10 normal hours where a GI worker brings 8 — so fewer of them clear the same
 * overflow. The page shows both numbers side by side so the trade-off is
 * visible rather than asserted.
 */
import { ThunderboltOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Col, Descriptions, Empty, Form, InputNumber, Row,
  Select, Statistic, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'

type Row = Record<string, unknown>

const errMsg = (e: unknown): string => {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}
const n2 = (v: unknown) => (v == null ? '—' : Number(v).toFixed(2))
const n0 = (v: unknown) => (v == null ? '—' : Math.round(Number(v)).toLocaleString())

export default function ManpowerPlanner() {
  const [form] = Form.useForm()
  const [result, setResult] = useState<Row | null>(null)

  const targets = useQuery({
    queryKey: ['/mh/planner/targets'],
    queryFn: async () => (await api.get<{ items: Row[]; surface_prep: Row[] }>(
      '/mh/planner/targets')).data,
  })

  const run = useMutation({
    mutationFn: (b: Row) => api.post('/mh/planner', b).then((r) => r.data),
    onSuccess: (d) => setResult(d as Row),
  })

  const options = [
    {
      label: 'Lining work (area still outstanding)',
      options: (targets.data?.items ?? []).map((t) => ({
        value: `${t.Equipment_Tag_No}|${t.Lining_System_Code}`,
        label: `${t.Equipment_Tag_No} · ${t.Lining_System_Code} — ${n2(t.Remaining_SQM)} m² left`,
      })),
    },
    {
      label: 'Surface prep (no lining system)',
      options: (targets.data?.surface_prep ?? []).map((t) => ({
        value: `${t.Equipment_Tag_No}|`,
        label: `${t.Equipment_Tag_No} · surface prep`,
      })),
    },
  ]

  const submit = async () => {
    const v = await form.validateFields()
    const [tag, code] = String(v.target).split('|')
    run.mutate({ equipment_tag: tag, lining_system_code: code ?? '',
                 deadline_hours: v.deadline_hours })
  }

  const inputs = result?.inputs as Row | undefined
  const workload = result?.workload as Row | undefined
  const req = result?.requirement as Row | undefined
  const strat = result?.strategy as Row | undefined
  const roster = result?.roster as Row | undefined
  const warnings = (result?.warnings as string[]) ?? []

  const activityCols: ColumnsType<Row> = [
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 130 },
    { title: 'Activity', dataIndex: 'Activity', width: 200 },
    { title: 'Variant', dataIndex: 'Variant_Key', width: 120,
      render: (v: string) => v ? <Tag color="purple">{v}</Tag> : '—' },
    { title: 'Benchmark crew', dataIndex: 'Benchmark_Crew_Size', width: 120, align: 'right' },
    { title: 'm²/shift', dataIndex: 'Standard_Productivity_Per_Shift', width: 100, align: 'right' },
    { title: 'Man-hrs per m²', dataIndex: 'Manhours_Per_SQM', width: 130, align: 'right',
      render: (v: unknown) => (
        <Tooltip title="Derived from man-hours ÷ productivity per shift, not from
          the workbook's rounded m²/hr/person column — that column is 2 d.p. and
          overstates tile lining by about 3.6%.">
          <span>{n2(v)}</span>
        </Tooltip>) },
    { title: 'Required man-hrs', dataIndex: 'Required_Manhours', width: 140, align: 'right',
      render: n0 },
    { title: 'Headcount for deadline', dataIndex: 'Required_Headcount', width: 160,
      align: 'right', render: n2 },
  ]

  const gapCols: ColumnsType<Row> = [
    { title: 'Role', dataIndex: 'Role_Code', width: 170 },
    { title: 'Required man-hrs', dataIndex: 'Required_Manhours', width: 140,
      align: 'right', render: n0 },
    { title: 'Required head', dataIndex: 'Required_Headcount_Rounded', width: 120,
      align: 'right' },
    { title: 'Available', dataIndex: 'Available_Headcount', width: 100, align: 'right' },
    { title: 'of which GI', dataIndex: 'Available_GI', width: 100, align: 'right' },
    { title: 'of which Non-GI', dataIndex: 'Available_NON_GI', width: 120, align: 'right' },
    { title: 'Day', dataIndex: 'Available_Day', width: 70, align: 'right' },
    { title: 'Night', dataIndex: 'Available_Night', width: 70, align: 'right' },
    { title: 'To procure', dataIndex: 'To_Procure', width: 110, align: 'right',
      render: (v: unknown) => {
        const x = Number(v ?? 0)
        return x > 0 ? <Tag color="red">{x}</Tag> : <Tag color="green">0</Tag>
      } },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Works out the labour needed to finish a job by a deadline, what the
        roster already covers, and how to remove the overtime. It is advice —
        nothing here changes the roster or assigns anybody.
      </Typography.Paragraph>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" initialValues={{ deadline_hours: 11 }}
          onFinish={submit}>
          <Form.Item name="target" label="Job" rules={[{ required: true }]}
            style={{ minWidth: 420 }}>
            <Select showSearch options={options} loading={targets.isFetching}
              placeholder="Equipment and system" optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="deadline_hours" label="Hours available per person"
            rules={[{ required: true }]}
            tooltip="The window each worker can put in. 11 h is one full shift
              (12 hours less an hour for lunch); 22 h is two.">
            <InputNumber min={0.5} step={1} style={{ width: 140 }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<ThunderboltOutlined />}
            loading={run.isPending}>Plan</Button>
        </Form>
      </Card>

      {run.isError && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message={errMsg(run.error)} />
      )}

      {!result && !run.isPending && (
        <Empty description="Pick a job and a deadline to see the plan" />
      )}

      {result && (
        <>
          {warnings.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 16 }}
              message="Read these before trusting the numbers"
              description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>} />
          )}

          {/* ── 1. workload ────────────────────────────────────────────── */}
          <Typography.Title level={5}>1 · Workload and required hours</Typography.Title>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Area remaining" value={n2(workload?.remaining_sqm)}
                suffix="m²" /></Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Required man-hours"
                value={n0(req?.Total_Required_Manhours)} /></Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Headcount for the deadline"
                value={n2(req?.Total_Required_Headcount)} /></Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Shifts in the window"
                value={n2(inputs?.shifts_in_window)} /></Card></Col>
          </Row>
          <Descriptions size="small" column={3} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="Job">
              {String(inputs?.equipment_tag)}{' '}
              {inputs?.system_agnostic
                ? <Tag color="gold">surface prep</Tag>
                : <Tag>{String(inputs?.lining_system_code)}</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Area source">
              {String(workload?.source ?? '—')}
            </Descriptions.Item>
            <Descriptions.Item label="Done so far">
              {n2(workload?.done_sqm)} of {n2(workload?.original_sqm)} m²
            </Descriptions.Item>
          </Descriptions>
          <Table size="small" pagination={false} columns={activityCols}
            dataSource={(result.activities as Row[]) ?? []}
            rowKey={(r) => `${r.Execution_Sub_Activity_Code}|${r.Variant_Key}`}
            scroll={{ x: 'max-content' }} style={{ marginBottom: 20 }} />

          {/* ── 2. gap ─────────────────────────────────────────────────── */}
          <Typography.Title level={5}>2 · Roster versus gap, by role</Typography.Title>
          {roster?.Unmapped && Object.keys(roster.Unmapped as object).length > 0 && (
            <Alert type="info" showIcon style={{ marginBottom: 8 }}
              message="Some workers are not counted as available"
              description="Their Designation matches no role in the master.
                They are shown as unmatched rather than assumed absent —
                'nobody wrote down that they are masons' and 'there are no
                masons' call for completely different actions." />
          )}
          <Table size="small" pagination={false} columns={gapCols}
            dataSource={(result.gap as Row[]) ?? []}
            rowKey={(r) => String(r.Role_Code)}
            scroll={{ x: 'max-content' }} style={{ marginBottom: 20 }} />

          {/* ── 3. strategy ────────────────────────────────────────────── */}
          <Typography.Title level={5}>3 · Overtime strategy</Typography.Title>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Normal capacity"
                value={n0(strat?.Normal_Capacity_Manhours)} suffix="man-hrs" />
            </Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Overtime incurred"
                valueStyle={{ color: Number(strat?.Overtime_Hours_Incurred ?? 0) > 0
                  ? '#cf1322' : '#3f8600' }}
                value={n0(strat?.Overtime_Hours_Incurred)} suffix="man-hrs" />
            </Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Unmet"
                valueStyle={{ color: Number(strat?.Unmet_Manhours ?? 0) > 0
                  ? '#cf1322' : '#3f8600' }}
                value={n0(strat?.Unmet_Manhours)} suffix="man-hrs" />
            </Card></Col>
            <Col xs={12} md={6}><Card size="small">
              <Statistic title="Deadline reachable?"
                valueStyle={{ color: strat?.Feasible ? '#3f8600' : '#cf1322' }}
                value={strat?.Feasible ? 'Yes' : 'No'} />
            </Card></Col>
          </Row>
          <Alert
            type={strat?.Feasible ? 'success' : 'error'} showIcon
            style={{ marginBottom: 12 }}
            message="Recommendation"
            description={String(strat?.Recommendation ?? '')} />
          {Number(strat?.Hire_NON_GI_To_Clear_Overtime ?? 0) > 0 && (
            <Row gutter={12}>
              <Col xs={24} md={12}>
                <Card size="small" title="Hire Non-GI (recommended)">
                  <Statistic value={String(strat?.Hire_NON_GI_To_Clear_Overtime)}
                    suffix="worker(s) to clear the overtime" />
                  <Typography.Paragraph type="secondary"
                    style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                    Each absorbs {String((inputs?.ot_thresholds as Row)?.NON_GI ?? 10)} h
                    before overtime begins.
                  </Typography.Paragraph>
                </Card>
              </Col>
              <Col xs={24} md={12}>
                <Card size="small" title="…or hire GI">
                  <Statistic value={String(strat?.Hire_GI_To_Clear_Overtime)}
                    suffix="worker(s) for the same result" />
                  <Typography.Paragraph type="secondary"
                    style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                    Each absorbs only {String((inputs?.ot_thresholds as Row)?.GI ?? 8)} h,
                    so more of them are needed to remove the same overtime.
                  </Typography.Paragraph>
                </Card>
              </Col>
            </Row>
          )}
        </>
      )}
    </div>
  )
}
