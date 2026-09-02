import { useState } from 'react'
import { App, Alert, Button, Form, Input, Typography } from 'antd'
import { SafetyOutlined } from '@ant-design/icons'
import { api } from '../api/client'

/**
 * The panel a mandated user meets INSTEAD of a session (Phase 10 Track 1).
 *
 * ⚠️ IT DRIVES /auth/2fa/* WITH A TOKEN THAT IS NOT A SESSION. The server mints
 * an `enroll`-scoped JWT after a correct password when the role requires 2FA,
 * the deadline has passed and the account has no authenticator. `_decode`
 * matches scope exactly, so that token opens the three enrolment routes and
 * nothing else — it is deliberately NOT passed to `setAuthToken`, because
 * storing it as a session would make "you must set up 2FA" a way to skip 2FA.
 *
 * ⚠️ AND THE PASSWORD IS ASKED FOR AGAIN. `/auth/2fa/enroll` requires a step-up
 * password check (audit A03-F8) so a stolen token cannot bind a new
 * authenticator to somebody's account. That was true before this flow existed
 * and stays true inside it: this panel is a new door to the same room, not a
 * second room with a weaker lock.
 *
 * The flow deliberately ends WITHOUT signing the user in. They enrol, then sign
 * in again through the TOTP challenge — which also proves the authenticator
 * they just bound actually produces codes the server accepts.
 */
export default function MandatoryEnrollPanel(
  { token, enforcedFrom, onDone }:
  { token: string; enforcedFrom: string | null; onDone: () => void },
) {
  const { message } = App.useApp()
  const [pending, setPending] = useState<{ qr: string; secret: string } | null>(null)
  const [busy, setBusy] = useState(false)

  // The enrol token travels per-request rather than through the shared client
  // default, so it can never leak into an ordinary API call.
  const auth = { headers: { Authorization: `Bearer ${token}` } }

  const start = async (v: { password: string }) => {
    setBusy(true)
    try {
      const { data } = await api.post('/auth/2fa/enroll', { password: v.password }, auth)
      setPending({ qr: data.qr, secret: data.secret })
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Could not start enrolment')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (v: { code: string }) => {
    setBusy(true)
    try {
      await api.post('/auth/2fa/verify', { code: v.code }, auth)
      onDone()
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'That code was not accepted')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="Two-factor authentication is required"
        description={
          enforcedFrom
            ? `Your role has required an authenticator app since ${enforcedFrom}. Set one up to continue.`
            : 'Your role requires an authenticator app. Set one up to continue.'
        }
      />
      {!pending ? (
        <Form layout="vertical" onFinish={start}>
          <Typography.Paragraph style={{ fontSize: 13 }}>
            Confirm your password to begin.
          </Typography.Paragraph>
          <Form.Item name="password" rules={[{ required: true, message: 'Password' }]}>
            <Input.Password placeholder="Password" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>
            Set up 2FA
          </Button>
        </Form>
      ) : (
        <Form layout="vertical" onFinish={confirm}>
          <Typography.Paragraph style={{ fontSize: 13 }}>
            Scan this with Google Authenticator, Microsoft Authenticator or 1Password,
            then enter the 6-digit code it shows.
          </Typography.Paragraph>
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            <img src={pending.qr} alt="2FA QR code" width={180} height={180} />
          </div>
          <Typography.Paragraph
            copyable={{ text: pending.secret }}
            style={{ fontSize: 12, textAlign: 'center' }}
          >
            {/* The manual-entry fallback, for a phone that cannot scan. */}
            <code>{pending.secret}</code>
          </Typography.Paragraph>
          <Form.Item name="code" rules={[{ required: true, message: '6-digit code' }]}>
            <Input prefix={<SafetyOutlined />} placeholder="Authenticator code" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>
            Turn on 2FA
          </Button>
        </Form>
      )}
    </div>
  )
}
