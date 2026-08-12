/**
 * Both halves of the issue gate, shown BEFORE the store keeper submits.
 *
 * A Surface Shield can be refused at issue for two unrelated reasons — no
 * Material Test Certificate on file (paperwork, fixed by Logistics) and no QC
 * approval (inspection, fixed by the site QC). They are fixed by different
 * people, so a banner that mentions only one sends the SK to chase the wrong
 * person, and they come back and get refused again by the other.
 *
 * The upload is here on purpose. Since 2026-08-12 the certificate is the thing
 * that blocks issue, and if the SK could only attach one from the RECEIVE form
 * then a shield already sitting in stock would be permanently stuck: the goods
 * are in, so there is no receipt left to attach it to. Usually they will not
 * need it — `mtc_source` shows the certificate inherited from the PO or DN.
 */
import { useState } from 'react'
import { Alert, App, Button, Space, Tag, Upload } from 'antd'
import { PaperClipOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import { useQcClearance } from '../api/hooks'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Upload failed'
}

export default function QcClearanceBanner({ sap, site }: { sap?: string; site?: string }) {
  const { message } = App.useApp()
  const { data, refetch } = useQcClearance(sap, site)
  const [busy, setBusy] = useState(false)

  // Not a controlled material (the other ~430) — say nothing at all.
  if (!data || !data.controlled) return null

  const mtcOk = data.mtc_ok as boolean
  const qtyOk = Number(data.available_for_issue ?? 0) > 0

  const upload = (
    <Upload showUploadList={false} accept=".pdf,.jpg,.jpeg,.png"
      customRequest={async ({ file, onSuccess, onError }) => {
        const fd = new FormData()
        fd.append('file', file as Blob)
        fd.append('sap_code', String(sap ?? ''))
        if (site) fd.append('site_id', String(site))
        setBusy(true)
        try {
          const r = await api.post<{ id: number }>('/entry/mtc', fd)
          message.success('MTC attached — this material is now cleared for issue here')
          await refetch()
          onSuccess?.(r.data)
        } catch (e) { message.error(errMsg(e)); onError?.(e as Error) } finally { setBusy(false) }
      }}>
      <Button size="small" loading={busy} icon={<PaperClipOutlined />}>Upload MTC</Button>
    </Upload>
  )

  if (mtcOk && qtyOk) {
    return (
      <Alert type="success" showIcon style={{ marginBottom: 12 }}
        title={`Cleared for issue: ${Number(data.available_for_issue).toLocaleString()} unit(s)`}
        description={<Space wrap size={4}>
          <Tag color="green">MTC on file</Tag>
          <span>{String(data.mtc_label ?? '')}</span>
        </Space>} />
    )
  }

  return (
    <Alert type="warning" showIcon style={{ marginBottom: 12 }}
      title="This Surface Shield cannot be issued yet"
      description={
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          {!mtcOk && (
            <Space wrap size={6}>
              <Tag color="red">No MTC</Tag>
              <span>
                No Material Test Certificate covers this material at this site.
                Logistics can attach it to the purchase order, the warehouse to the
                delivery note, or upload it here.
              </span>
              {upload}
            </Space>
          )}
          {mtcOk && (
            <Space wrap size={6}>
              <Tag color="green">MTC on file</Tag>
              <span>{String(data.mtc_label ?? '')}</span>
            </Space>
          )}
          {!qtyOk && (
            <Space wrap size={6}>
              <Tag color="red">Not inspected</Tag>
              <span>
                {Number(data.inspections ?? 0) === 0
                  ? 'No quality inspection exists here yet — the site QC has to check the material.'
                  : `${Number(data.pending_inspections ?? 0)} inspection(s) still pending; `
                    + `${Number(data.approved_qty ?? 0)} approved and `
                    + `${Number(data.issued_qty ?? 0)} already issued or staged.`}
              </span>
            </Space>
          )}
        </Space>
      } />
  )
}
