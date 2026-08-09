/**
 * PpeRulesPage — the Store Keeper states how long each item of PPE lasts.
 *
 * This page is the ONLY source of a replacement date. The `PPE` category in
 * the master says which items offer the flow; it cannot say whether safety
 * shoes last six months and goggles three. An item listed here without a
 * usable time can still be issued and recorded — it simply has no expiry and
 * never reaches the order forecast, which is why that state is called out in
 * the table rather than left as an empty cell.
 *
 * A rule with no site is the GLOBAL default and any site may set it, on
 * purpose: usable time is a property of the product, not of the yard, and
 * making every site restate it is how half of them end up with no rule.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Form, InputNumber, Modal, Popconfirm, Select,
  Space, Switch, Tag, Typography,
} from 'antd'
import { SafetyOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import {
  useDeletePpeRule, usePpeEligible, usePpeRules, useSavePpeRule, useSites,
} from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// Named presets, because "180" is a number somebody has to convert in their
// head and "6 months" is the way the safety officer actually said it.
const PRESETS = [
  { label: '1 month', value: 30 }, { label: '3 months', value: 90 },
  { label: '6 months', value: 180 }, { label: '1 year', value: 365 },
]

export default function PpeRulesPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const { data: rules, isLoading } = usePpeRules()
  const { data: eligible } = usePpeEligible(user?.site_id || undefined)
  const { data: sites } = useSites()
  const save = useSavePpeRule()
  const del = useDeletePpeRule()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const unconfigured = (eligible ?? []).filter((r) => r.usable_days == null)

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const res = await save.mutateAsync({
        SAP_Code: v.SAP_Code, usable_days: Number(v.usable_days),
        site_id: v.site_id || undefined,
        requires_safety_doc: v.requires_safety_doc !== false,
        notes: v.notes,
      })
      message.success(String(res.note ?? 'Saved'))
      form.resetFields()
      setOpen(false)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'SAP_Code', key: 'sap', width: 110 },
    { title: 'Code', dataIndex: 'Material_Code', key: 'mat', width: 140 },
    { title: 'Description', dataIndex: 'Description', key: 'd', ellipsis: true },
    {
      title: 'Applies to', dataIndex: 'Site_ID', key: 'site', width: 140,
      render: (v) => (v ? <Tag color="blue">{String(v)}</Tag>
                        : <Tag>every site (default)</Tag>),
    },
    {
      title: 'Usable time', dataIndex: 'usable_days', key: 'days', align: 'right',
      width: 130,
      render: (v) => {
        const d = Number(v)
        const preset = PRESETS.find((p) => p.value === d)
        return <span>{d} days{preset ? ` · ${preset.label}` : ''}</span>
      },
    },
    {
      title: 'Safety doc', dataIndex: 'requires_safety_doc', key: 'doc', width: 110,
      render: (v) => (v ? <Tag color="green">required</Tag> : <Tag>optional</Tag>),
    },
    { title: 'Notes', dataIndex: 'notes', key: 'n', ellipsis: true,
      render: (v) => String(v ?? '') },
    {
      title: '', key: '__act', width: 90, align: 'right',
      render: (_, r) => (
        <Popconfirm
          title="Remove this rule?"
          description="Gear already issued keeps the expiry it was issued under."
          onConfirm={async () => {
            try {
              await del.mutateAsync(Number(r.id))
              message.success('Rule removed')
            } catch (e) { message.error(errMsg(e)) }
          }}
        >
          <Button size="small" danger disabled={readOnly}>Remove</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <SafetyOutlined /> PPE Usable Time
        </Typography.Title>
        <Typography.Text type="secondary">
          How long each item of protective equipment lasts before it should be
          replaced. This is what turns an issue into a replacement date, and a
          replacement date into the order forecast.
        </Typography.Text>
      </div>

      {unconfigured.length > 0 && (
        <Alert
          type="warning" showIcon
          title={`${unconfigured.length} PPE item(s) have no usable time set`}
          description={
            <span>
              They can still be issued and will be recorded against the
              employee — but with no replacement date, so they never appear in
              the order forecast:{' '}
              {unconfigured.slice(0, 8).map((r) => (
                <Tag key={String(r.SAP_Code)}>{String(r.SAP_Code)}</Tag>
              ))}
              {unconfigured.length > 8 && `+${unconfigured.length - 8} more`}
            </span>
          }
        />
      )}

      <Card
        size="small" title="Rules"
        extra={<Button type="primary" disabled={readOnly}
                       onClick={() => setOpen(true)}>Set a usable time</Button>}
      >
        <Table
          sticky={{ offsetHeader: 64 }} size="small" loading={isLoading}
          columns={columns} dataSource={rules ?? []}
          rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        />
      </Card>

      <Modal open={open} title="Usable time" onCancel={() => setOpen(false)}
             onOk={submit} okText="Save" confirmLoading={save.isPending}>
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          title="This applies to FUTURE issues only"
          description="Gear already handed out keeps the usable time it was issued under — shortening a rule must not retroactively expire somebody's boots."
        />
        <Form form={form} layout="vertical"
              initialValues={{ requires_safety_doc: true, usable_days: 180 }}>
          <Form.Item name="SAP_Code" label="Material"
                     rules={[{ required: true, message: 'pick a material' }]}>
            <Select
              showSearch optionFilterProp="label"
              placeholder="Search PPE materials"
              options={(eligible ?? []).map((r) => ({
                value: String(r.SAP_Code),
                label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`
                  + (r.usable_days == null ? '  (no rule yet)' : ''),
              }))}
            />
          </Form.Item>
          <Form.Item name="usable_days" label="Usable time (days)"
                     rules={[{ required: true }]}>
            <InputNumber min={1} max={3650} style={{ width: '100%' }}
                         addonAfter="days" />
          </Form.Item>
          <Space wrap style={{ marginTop: -12, marginBottom: 16 }}>
            {PRESETS.map((p) => (
              <Button key={p.value} size="small"
                      onClick={() => form.setFieldsValue({ usable_days: p.value })}>
                {p.label}
              </Button>
            ))}
          </Space>
          <Form.Item name="site_id" label="Applies to">
            <Select allowClear placeholder="Every site (the default)"
                    options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="requires_safety_doc" label="Signed safety approval required"
                     valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
