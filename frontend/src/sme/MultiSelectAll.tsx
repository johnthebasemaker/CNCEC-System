/**
 * frontend/src/sme/MultiSelectAll.tsx — a multi-select that can select all.
 *
 * ONE component rather than seven copies of the same `onChange`. The SME
 * filter bars carry seven `mode="multiple"` selects (Total Overview ×3,
 * Execution Plan ×3, Smart Calculator ×1) and every one of them had the same
 * gap: with 29 equipment tags or 30 system codes in the list, "look at
 * everything" meant 30 clicks or nothing.
 *
 * Two deliberate choices:
 *
 *  · **Select all means the CURRENT option list, not a magic token.** The
 *    value stays a plain array of the real values, so every consumer — the
 *    filter predicates, the exports, the URL state — keeps working unchanged
 *    and there is no sentinel for someone to forget to unwrap. It also means
 *    that if the options narrow (a site change repopulates them), a previously
 *    "all" selection is just a stale explicit list, which is honest: it shows
 *    exactly which values are still selected rather than silently re-expanding.
 *
 *  · **The header is a footer-style dropdownRender, not a fake option.** A
 *    "(Select all)" entry inside the list is one mis-click away from being
 *    treated as a real filter value, and it sorts and searches like one too.
 */
import type { ReactNode } from 'react'
import { Button, Divider, Select, Space, Typography } from 'antd'
import type { SelectProps } from 'antd'

export interface MultiSelectAllProps
  extends Omit<SelectProps<string[]>, 'mode' | 'dropdownRender'> {
  /** Full option list — "Select all" resolves to exactly these values. */
  options: { value: string; label?: ReactNode }[]
  value?: string[]
  onChange?: (v: string[]) => void
}

export default function MultiSelectAll({
  options, value, onChange, ...rest
}: MultiSelectAllProps) {
  const all = options.map((o) => o.value)
  const selected = value ?? []
  const isAll = all.length > 0 && selected.length === all.length

  return (
    <Select
      {...rest}
      mode="multiple"
      allowClear
      maxTagCount={rest.maxTagCount ?? 'responsive'}
      options={options}
      value={value}
      onChange={(v) => onChange?.(v as string[])}
      popupRender={(menu) => (
        <>
          <Space style={{ padding: '4px 8px', width: '100%',
                          justifyContent: 'space-between' }}>
            <Space size={4}>
              <Button size="small" type="link" disabled={isAll || !all.length}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onChange?.(all)}>
                Select all
              </Button>
              <Button size="small" type="link" disabled={!selected.length}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onChange?.([])}>
                Clear
              </Button>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {selected.length} of {all.length}
            </Typography.Text>
          </Space>
          <Divider style={{ margin: '4px 0' }} />
          {menu}
        </>
      )}
    />
  )
}
