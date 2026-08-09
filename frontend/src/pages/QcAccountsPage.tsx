/**
 * QcAccountsPage — create quality inspectors, and move them between sites.
 *
 * Two things a reader should know before editing this page.
 *
 * **The scope is not a form field.** An HOD creating an inspector does not
 * choose the site and a warehouse user does not choose the warehouse — the
 * server takes both from the ACTOR (`/qc/accounts`). If it were a field, a
 * level-1 caller could bind an account to a site they have no authority
 * over. The form shows the binding it is about to apply, read-only, so the
 * user is not surprised; it does not send it.
 *
 * **A transfer is two steps on purpose.** The HOD raises, an admin decides.
 * Approving rewrites `users.Site_ID`, and site_id rides inside the 15-minute
 * access token — so approval is also the moment the moved account's sessions
 * are revoked. The banner below says that out loud, because "why was I
 * logged out?" is otherwise a support ticket.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Form, Input, Modal, Space, Table as _T, Tag,
  Typography,
} from 'antd'
import { TeamOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import {
  useCreateQcAccount, useDecideQcTransfer, useQcAccounts, useQcTransfers,
  useRequestQcTransfer,
} from '../api/hooks'
import { useSites } from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

void _T

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const TRANSFER_TAG: Record<string, string> = {
  pending_admin: 'gold', approved: 'green', rejected: 'red', cancelled: 'default',
}

function CreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const { user } = useAuth()
  const create = useCreateQcAccount()
  const [form] = Form.useForm()
  const isLogistics = user?.role === 'logistics' || user?.role === 'admin'
  const { data: sites } = useSites()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const res = await create.mutateAsync({
        username: v.username.trim(),
        password: v.password,
        phone_number: v.phone_number?.trim() || undefined,
        // Only the oversight roles may name a binding; for everyone else the
        // server uses the actor's own and ignores anything sent here.
        ...(isLogistics
          ? { site_id: v.site_id || undefined, warehouse_id: v.warehouse_id || undefined }
          : {}),
      })
      message.success(
        `Created ${res.username} — ${res.site_id ? `site ${res.site_id}` : `warehouse ${res.warehouse_id}`}`)
      form.resetFields()
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal open={open} title="New quality inspector" onCancel={onClose} onOk={submit}
           okText="Create" confirmLoading={create.isPending}>
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item name="username" label="Username"
                   rules={[{ required: true, message: 'a username is required' }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="password" label="Temporary password"
                   rules={[{ required: true, min: 8,
                             message: 'at least 8 characters' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item name="phone_number" label="Phone (optional, +E.164)">
          <Input placeholder="+966…" autoComplete="off" />
        </Form.Item>
        {isLogistics ? (
          <>
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
                   title="Name exactly one of site or warehouse"
                   description="A quality inspector belongs to a site or to a warehouse — never both, and never neither." />
            <Form.Item name="site_id" label="Site">
              <Input list="qc-sites" placeholder="leave blank for a warehouse inspector" />
            </Form.Item>
            <datalist id="qc-sites">
              {(sites ?? []).map((s) => <option key={s} value={s} />)}
            </datalist>
            <Form.Item name="warehouse_id" label="Warehouse">
              <Input placeholder="leave blank for a site inspector" />
            </Form.Item>
          </>
        ) : (
          <Alert
            type="info" showIcon
            title={user?.role === 'hod'
              ? `This inspector will be bound to your site: ${user?.site_id || '—'}`
              : `This inspector will be bound to your warehouse: ${user?.warehouse_id || '—'}`}
            description="The binding comes from your own account, not from this form."
          />
        )}
      </Form>
    </Modal>
  )
}

function TransferModal({ username, onClose }:
{ username: string | null; onClose: () => void }) {
  const { message } = App.useApp()
  const request = useRequestQcTransfer()
  const { data: sites } = useSites()
  const [form] = Form.useForm()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await request.mutateAsync({
        username: username!, to_site: v.to_site.trim(), reason: v.reason.trim(),
      })
      message.success('Transfer requested — an admin has to approve it')
      form.resetFields()
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal open={!!username} title={`Transfer ${username ?? ''}`} onCancel={onClose}
           onOk={submit} okText="Request transfer" confirmLoading={request.isPending}>
      <Alert
        type="warning" showIcon style={{ marginBottom: 16 }}
        title="An admin has to approve this"
        description="When they do, the inspector is signed out of every device — their site is carried inside their session token, so a fresh sign-in is what picks up the new one."
      />
      <Form form={form} layout="vertical">
        <Form.Item name="to_site" label="Move to site"
                   rules={[{ required: true, message: 'pick a site' }]}>
          <Input list="qc-transfer-sites" />
        </Form.Item>
        <datalist id="qc-transfer-sites">
          {(sites ?? []).map((s) => <option key={s} value={s} />)}
        </datalist>
        <Form.Item name="reason" label="Reason"
                   rules={[{ required: true, min: 3, message: 'say why' }]}>
          <Input.TextArea rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function QcAccountsPage() {
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const { message } = App.useApp()
  const { data: accounts, isLoading } = useQcAccounts()
  const { data: transfers } = useQcTransfers()
  const decide = useDecideQcTransfer()
  const [creating, setCreating] = useState(false)
  const [transferring, setTransferring] = useState<string | null>(null)

  const isHod = user?.role === 'hod'
  const isAdmin = user?.role === 'admin'

  const accountCols: ColumnsType<Row> = [
    { title: 'Username', dataIndex: 'username', key: 'u' },
    {
      title: 'Bound to', key: 'bind',
      render: (_, r) => (r.Site_ID
        ? <Tag color="blue">site {String(r.Site_ID)}</Tag>
        : <Tag color="purple">warehouse {String(r.Warehouse_ID ?? '—')}</Tag>),
    },
    { title: 'Phone', dataIndex: 'Phone_Number', key: 'p',
      render: (v) => String(v ?? '—') },
    {
      title: 'Action', key: '__act', width: 130,
      render: (_, r) => (
        <Button
          size="small"
          // Only an HOD transfers, and only a SITE inspector can be
          // site-transferred — a warehouse one is re-bound by an admin,
          // because moving it changes which warehouse's goods it inspects.
          disabled={!isHod || readOnly || !r.Site_ID}
          onClick={() => setTransferring(String(r.username))}
        >
          Transfer
        </Button>
      ),
    },
  ]

  const transferCols: ColumnsType<Row> = [
    { title: 'Inspector', dataIndex: 'username', key: 'u' },
    { title: 'From', dataIndex: 'from_site', key: 'f', render: (v) => String(v ?? '—') },
    { title: 'To', dataIndex: 'to_site', key: 't' },
    { title: 'Reason', dataIndex: 'reason', key: 'r', ellipsis: true },
    { title: 'Raised by', dataIndex: 'requested_by', key: 'rb' },
    {
      title: 'Status', dataIndex: 'status', key: 's', width: 140,
      render: (v: string) => <Tag color={TRANSFER_TAG[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: 'Decision', key: '__d', width: 170,
      render: (_, r) => r.status !== 'pending_admin' ? null : (
        <Space size="small">
          <Button
            type="primary" size="small" disabled={!isAdmin || readOnly}
            onClick={async () => {
              try {
                const res = await decide.mutateAsync({ id: Number(r.id), action: 'approve' })
                message.success(
                  `${res.username} moved to ${res.to_site} — ${res.sessions_revoked} session(s) ended`)
              } catch (e) { message.error(errMsg(e)) }
            }}
          >
            Approve
          </Button>
          <Button
            danger size="small" disabled={!isAdmin || readOnly}
            onClick={async () => {
              try {
                await decide.mutateAsync({ id: Number(r.id), action: 'reject' })
                message.success('Transfer rejected')
              } catch (e) { message.error(errMsg(e)) }
            }}
          >
            Reject
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <TeamOutlined /> QC Accounts
        </Typography.Title>
        <Typography.Text type="secondary">
          A quality inspector belongs to one site or to one warehouse, and that
          binding decides everything they can see. Create them here rather than
          waiting on an admin.
        </Typography.Text>
      </div>

      <Card
        size="small"
        title="Inspectors"
        extra={
          <Button type="primary" disabled={readOnly} onClick={() => setCreating(true)}>
            New inspector
          </Button>
        }
      >
        <Table
          sticky={{ offsetHeader: 64 }}
          size="small" loading={isLoading} columns={accountCols}
          dataSource={accounts ?? []} rowKey={(r) => String(r.username)}
        />
      </Card>

      <Card size="small" title="Transfers">
        {isAdmin && (
          <Alert
            type="info" showIcon style={{ marginBottom: 12 }}
            title="Approving a transfer signs the inspector out everywhere"
            description="Their site is carried inside their session token, so revoking is what makes the move take effect immediately rather than in up to fifteen minutes."
          />
        )}
        <Table
          size="small" columns={transferCols} dataSource={transfers ?? []}
          rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        />
      </Card>

      <CreateModal open={creating} onClose={() => setCreating(false)} />
      <TransferModal username={transferring} onClose={() => setTransferring(null)} />
    </Space>
  )
}
