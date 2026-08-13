// Entity metadata driving the generic table + CRUD screens. Mirrors the backend
// (backend/api/main.py ENTITIES). Read entities are browse-only; write entities
// are the master-data tables the API exposes POST/PUT/DELETE for.

// Access rule per read entity — mirrors the legacy visibility of each log.
// Shape matches AccessRule in nav.tsx (kept structural to avoid a circular import).
export type EntityAccess = { anyRole: string[] } | { minLevel: number }

export interface ReadEntity {
  key: string
  label: string
  path: string
  hasSite: boolean
  access: EntityAccess
}

export interface Field {
  name: string
  label: string
  required?: boolean
  type?: 'text' | 'select'
  options?: string[]
}

export interface WriteEntity {
  key: string
  label: string
  path: string
  idKey: string
  fields: Field[]
  /**
   * Who may open this master-data editor. Defaults to `{ minLevel: 3 }` —
   * Logistics and admin — which is what master data has always meant here.
   * Set it only to make an entity STRICTER; see `employees`.
   */
  access?: EntityAccess
}

export const READ_ENTITIES: ReadEntity[] = [
  // inventory (stock list) is a benign, site-scoped read → all roles.
  { key: 'inventory', label: 'Inventory', path: '/inventory', hasSite: true, access: { minLevel: 0 } },
  // ledger logs are an oversight surface → hod+ (SK reviews its own entries in
  // the Data Entry staging grid, not here).
  { key: 'receipts', label: 'Receipts', path: '/receipts', hasSite: true, access: { minLevel: 2 } },
  { key: 'consumption', label: 'Consumption', path: '/consumption', hasSite: true, access: { minLevel: 2 } },
  { key: 'returns', label: 'Returns', path: '/returns', hasSite: true, access: { minLevel: 2 } },
  { key: 'lots', label: 'Lots', path: '/lots', hasSite: true, access: { minLevel: 2 } },
  // POs: was `minLevel: 3` (logistics/auditor/admin). The warehouse user
  // receives goods AGAINST a purchase order and could not look one up, which
  // is why they were phoning Logistics to have line quantities read out to
  // them (2026-08-12). Named roles rather than a level, because "level 3 and
  // above" is not the reason any of these three need it.
  { key: 'purchase-orders', label: 'Purchase Orders', path: '/purchase-orders', hasSite: true, access: { anyRole: ['warehouse_user', 'logistics', 'auditor'] } },
  // Purchase Requests browse — same standard as the PO page (UAT Phase 2).
  // hod+ (HODs raise PRs; logistics/admin oversee them).
  { key: 'purchase-requests', label: 'Purchase Requests', path: '/purchase-requests', hasSite: true, access: { minLevel: 2 } },
  // Equipment: the auditor was the one read entity it had been missed from.
  // `/sme/*` is the source and it admits the auditor, so the API had always
  // allowed this read while the browse row alone hid it.
  { key: 'equipment', label: 'Equipment (SME)', path: '/equipment', hasSite: true, access: { anyRole: ['hod', 'auditor'] } },
]

const STATUS: Field = {
  name: 'status',
  label: 'Status',
  type: 'select',
  options: ['active', 'inactive'],
}

export const WRITE_ENTITIES: WriteEntity[] = [
  {
    key: 'vendors',
    label: 'Vendors',
    path: '/vendors',
    idKey: 'id',
    fields: [
      { name: 'Vendor_Code', label: 'Vendor Code', required: true },
      { name: 'Vendor_Name', label: 'Vendor Name', required: true },
      { name: 'Address', label: 'Address' },
      { name: 'Contact_Name', label: 'Contact Name' },
      { name: 'Contact_Phone', label: 'Contact Phone' },
      { name: 'Contact_Email', label: 'Contact Email' },
      STATUS,
    ],
  },
  {
    key: 'warehouses',
    label: 'Warehouses',
    path: '/warehouses',
    idKey: 'id',
    fields: [
      { name: 'Warehouse_ID', label: 'Warehouse ID', required: true },
      { name: 'Name', label: 'Name', required: true },
      { name: 'Location', label: 'Location' },
      { name: 'Contact_Name', label: 'Contact Name' },
      { name: 'Contact_Phone', label: 'Contact Phone' },
      { name: 'Contact_Email', label: 'Contact Email' },
      STATUS,
    ],
  },
  {
    key: 'employees',
    label: 'Employees',
    path: '/employees',
    idKey: 'id',
    // ⚠️ ADMIN ONLY, and the exception is deliberate (2026-08-12). Every other
    // master-data entity is Logistics + admin. The operator revoked the staff
    // roster from Logistics for worker privacy, and this editor is the SAME
    // table with create/update/delete on top — leaving it open would have made
    // that revocation cosmetic, since reading every name and phone number from
    // here takes one click. The HOD keeps the operation that matters
    // (transfers, on the Employees page); nobody has lost a workflow.
    access: { anyRole: [] },
    fields: [
      { name: 'ID_Number', label: 'ID Number', required: true },
      { name: 'Name', label: 'Name', required: true },
      { name: 'Phone_Number', label: 'Phone Number' },
      { name: 'Department', label: 'Department' },
      { name: 'Site_ID', label: 'Site' },
      STATUS,
    ],
  },
]
