/**
 * PpeIssueFields — the two-and-a-bit fields the ORDINARY issue form grows
 * when the material a Store Keeper picked turns out to be PPE.
 *
 * Option A (operator ruling, 2026-08-09): there is ONE issue form. Safety
 * goggles go out the same way as everything else, and the difference is that
 * this panel appears — asking who is receiving them and for the signed safety
 * approval — and the backend writes a `ppe_distributions` row beside the
 * ordinary stock movement.
 *
 * Three things this component is careful about:
 *
 * 1. **It renders nothing at all for non-PPE materials.** ~450 of the 466
 *    materials in the master are not PPE, and the issue form must look
 *    exactly as it did for them.
 *
 * 2. **It warns BEFORE submit, not after.** Once an employee ID is typed the
 *    panel fetches that person's history and says, in words, what they
 *    already hold and until when. The backend enforces the rule regardless
 *    (this is a courtesy, not the boundary) — but finding out you need a
 *    reason only when the submit fails is a bad afternoon.
 *
 * 3. **The history it reads is the PERSON'S, across every site.** A worker
 *    who transferred in last week still shows the boots they were issued at
 *    their old site, which is the entire point of ruling R1 and the reason
 *    they do not get a second pair on their first morning.
 */
import { Alert, Col, Form, Input, Row, Space, Tag, Typography } from 'antd'
import { SafetyOutlined } from '@ant-design/icons'
import EntryDocsUpload from './EntryDocsUpload'
import { usePpeEmployee } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
// The pure helpers live in lib/ppe.ts so this module exports a component and
// nothing else — fast refresh stops working on a file that mixes the two.
import { activeHolding } from '../lib/ppe'
import type { PpeRule, PpeState } from '../lib/ppe'

export default function PpeIssueFields({
  rule, siteId, value, onChange,
}: {
  rule: PpeRule | null
  siteId?: string
  value: PpeState
  onChange: (next: PpeState) => void
}) {
  // Hooks must run unconditionally, so the early return lives BELOW them.
  const { data: history, isFetching, isError } = usePpeEmployee(value.employeeId)
  if (!rule) return null

  const held = activeHolding(history, rule.SAP_Code)
  const today = new Date().toISOString().slice(0, 10)
  const stillGood = !!(held?.expires_on && String(held.expires_on) > today)
  const person = (history?.employee as ApiRow | undefined)?.Name as string | undefined

  return (
    <Alert
      type="info"
      icon={<SafetyOutlined />}
      showIcon
      style={{ marginBottom: 16 }}
      title="This is PPE — record who is receiving it"
      description={
        <Space orientation="vertical" size="small" style={{ width: '100%', marginTop: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {rule.usable_days
              ? `Usable time on file: ${rule.usable_days} days — the replacement date is `
                + 'calculated from the issue date.'
              : 'No usable time is configured for this item, so it will be recorded '
                + 'without a replacement date and will not appear in the order forecast. '
                + 'Set one on the PPE Rules page.'}
          </Typography.Text>

          <Row gutter={16} style={{ width: '100%' }}>
            <Col xs={24} md={10}>
              <Form.Item
                label="Employee ID receiving it" required style={{ marginBottom: 8 }}
                validateStatus={value.employeeId && isError ? 'error' : undefined}
                help={value.employeeId && isError
                  ? 'No employee at your site has that ID'
                  : person ? `✓ ${person}` : undefined}
              >
                <Input
                  value={value.employeeId} placeholder="e.g. 30551"
                  onChange={(e) => onChange({ ...value, employeeId: e.target.value })}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={14}>
              {/* No Form.Item label here: the uploader renders its own
                  heading ("Signed safety approval (required — …)"), and two
                  labels stacked on one control reads as two controls. */}
              <EntryDocsUpload
                docType="safety_approval" siteId={siteId}
                value={value.doc} required={rule.requires_safety_doc}
                onChange={(docs) => onChange({ ...value, doc: docs })}
              />
            </Col>
          </Row>

          {isFetching && !history && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Checking what they already hold…
            </Typography.Text>
          )}

          {held && (
            <Alert
              type={stillGood ? 'warning' : 'success'}
              showIcon
              title={stillGood
                ? `${person ?? 'This worker'} already holds this item`
                : `${person ?? 'This worker'} is due a replacement`}
              description={
                <Space orientation="vertical" size={2}>
                  <span>
                    Issued {String(held.issued_on)} at{' '}
                    <Tag>{String(held.Site_ID)}</Tag>
                    {held.expires_on
                      ? stillGood
                        ? ` · good until ${String(held.expires_on)}`
                        : ` · was due ${String(held.expires_on)}`
                      : ' · no replacement date on record'}
                  </span>
                  {String(held.Site_ID) !== String(siteId ?? '') && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      Issued at another site — their history follows them, so this
                      would be a replacement rather than a first issue.
                    </Typography.Text>
                  )}
                </Space>
              }
            />
          )}

          {stillGood && (
            <Form.Item
              label="Reason for replacing it early (required)"
              required style={{ marginBottom: 0 }}
              validateStatus={value.earlyReason.trim() ? undefined : 'warning'}
            >
              <Input.TextArea
                rows={2} value={value.earlyReason}
                placeholder="Damaged, lost, wrong size…"
                onChange={(e) => onChange({ ...value, earlyReason: e.target.value })}
              />
            </Form.Item>
          )}
        </Space>
      }
    />
  )
}
