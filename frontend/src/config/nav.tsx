/**
 * frontend/src/config/nav.tsx — the ONE source of truth for navigation access.
 *
 * Ports the legacy visibility model (`_can_access` in main.py:230 =
 * PAGE_ACCESS + _EXACT_ROLE_PAGES + _PAGE_BLOCKED_ROLES) into a single
 * declarative manifest that drives:
 *   1. the sidebar (AppLayout),
 *   2. client-side route guards (AppLayout redirects on denied paths),
 *   3. (Phase 3) the ⌘K command palette.
 *
 * The API already enforces every endpoint's role gate; this makes the UI agree
 * with it instead of leaking pages a role can open but not use.
 *
 * ADMIN SHADOW: admin may reach ANY page (legacy "lands via shadow"), but its
 * DEFAULT sidebar is a curated set — operational groups are revealed via the
 * "All areas" toggle (see ADMIN_DEFAULT_GROUPS). This mirrors legacy hiding
 * exact-locked pages from the admin sidebar while still allowing navigation.
 */
import type { ReactNode } from 'react'
import {
  AlertOutlined, AuditOutlined, BarChartOutlined, CameraOutlined, CarOutlined,
  DashboardOutlined, DatabaseOutlined, ExperimentOutlined, FallOutlined,
  FieldTimeOutlined, FileExcelOutlined, FileProtectOutlined, FileSearchOutlined, FireOutlined,
  FormOutlined, FundProjectionScreenOutlined, InboxOutlined, MessageOutlined, ProfileOutlined,
  SafetyCertificateOutlined, SolutionOutlined, StockOutlined, TeamOutlined,
  ToolOutlined, ControlOutlined, UserAddOutlined,
} from '@ant-design/icons'
import type { User } from '../auth/AuthContext'
import { isReadOnly } from '../auth/readOnly'
import { READ_ENTITIES, WRITE_ENTITIES, type ReadEntity } from './entities'

// An access rule: either an exact set of roles (admin implicitly allowed), or a
// minimum hierarchy level. `minLevel` reproduces the legacy cascading checks;
// `anyRole` reproduces the exact-locks.
//
// `writes` marks a page whose PURPOSE is changing data — a form, an approval
// queue, an importer, a CRUD editor. A view-only role (Auditor) cannot open
// one at all, which is what removes the great majority of Edit/Upload/Sync/
// Delete controls from its UI without touching a single page component. Pages
// that merely CONTAIN an action (Documents has an upload button) are NOT
// marked; those controls are disabled individually via useReadOnly().
export type AccessRule = ({ anyRole: string[] } | { minLevel: number })
  & { writes?: boolean }

export interface NavNode {
  key: string          // route path (also the antd menu key)
  label: string
  icon?: ReactNode
  access: AccessRule
  badge?: string       // key into the work-queues map → gold count badge
  redBadge?: boolean   // SLA/urgency → red count badge (admin overdue)
}

export interface NavGroup {
  id: string           // stable group id (used by ADMIN_DEFAULT_GROUPS)
  label?: string       // undefined → ungrouped top items
  access?: AccessRule  // group-level gate; a node still needs its own rule
  children: NavNode[]
}

/** Convenience: an access rule that also forbids view-only roles. */
const w = <T extends object>(rule: T): T & { writes: true } =>
  ({ ...rule, writes: true })

// ── the manifest ────────────────────────────────────────────────────────────
// Access rules chosen to reproduce legacy exactly (see config.py PAGE_ACCESS /
// main.py _EXACT_ROLE_PAGES). Levels: SK 0 · warehouse/supervisor 1 · hod 2 ·
// logistics 3 · admin 4.
export const NAV: NavGroup[] = [
  {
    id: 'overview',
    children: [
      // Legacy Live Dashboard = supervisor+ (SK never saw it). SK lands on its
      // role-home instead (see ROLE_HOME + the index redirect in AppLayout).
      { key: '/', label: 'Dashboard', icon: <DashboardOutlined />, access: { minLevel: 1 } },
      { key: '/stock', label: 'Stock', icon: <StockOutlined />, access: { minLevel: 1 } },
    ],
  },
  {
    id: 'entry',
    label: 'Data Entry',
    access: w({ anyRole: ['store_keeper'] }),   // Entry Log exact-locked to SK
    children: [
      { key: '/entry/receive', label: 'Receive Stock', icon: <FormOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/entry/issue', label: 'Issue Stock', icon: <FormOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/entry/return', label: 'Return Stock', icon: <FormOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/entry/adjust', label: 'Stock Adjustment', icon: <FormOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/entry/count', label: 'Stock Count', icon: <FormOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/entry/returnables', label: 'Returnable Items', icon: <ToolOutlined />, access: { anyRole: ['store_keeper'] }, badge: 'returnables_overdue' },
      { key: '/entry/ocr', label: 'OCR Import', icon: <CameraOutlined />, access: { anyRole: ['store_keeper'] } },
      { key: '/site/incoming', label: 'Incoming Deliveries', icon: <InboxOutlined />, access: { anyRole: ['store_keeper'] }, badge: 'incoming_dns' },
      { key: '/sk/requests', label: 'Supervisor Requests', icon: <SolutionOutlined />, access: { anyRole: ['store_keeper'] }, badge: 'sk_requests' },
    ],
  },
  {
    id: 'records',
    label: 'Records',
    // Per-entity access (inventory is all-roles; ledger logs are hod+; POs are
    // logistics+; equipment is the SME read-lock). This is what stops "Issue &
    // Receipt Log" showing to every role.
    children: READ_ENTITIES.map((e) => ({
      key: `/records/${e.key}`,
      label: e.label,
      access: e.access,
    })),
  },
  {
    // 2026-08-04: `auditor` reads the HOD portal.
    //
    // The API was never the thing keeping it out — /hod/* is
    // `Depends(require_level(2))` and an auditor is level 3, so the backend
    // has always allowed these reads. Only this manifest hid them, which made
    // "the Auditor cannot see HOD" a UI accident rather than a policy.
    //
    // Added to the READ children only. Anything wrapped in `w()` stays
    // {hod, admin}: a `writes: true` page is unreachable for a view-only role
    // by design, and that is the mechanism that strips the Approve / Import
    // controls without touching a single page component. The write guard in
    // readonly.py is untouched and nothing is added to its allowlist — an
    // auditor that reached one of these pages still cannot POST from it.
    id: 'hod',
    label: 'HOD',
    access: { anyRole: ['hod', 'auditor'] },
    children: [
      { key: '/hod/executive-summary', label: 'Executive Summary', icon: <FundProjectionScreenOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/approvals', label: 'Approvals', icon: <AuditOutlined />, access: w({ anyRole: ['hod'] }), badge: 'approvals' },
      { key: '/hod/burn-rate', label: 'Burn Rate', icon: <FireOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/lining-coverage', label: 'Lining Coverage', icon: <ExperimentOutlined />, access: { anyRole: ['hod', 'logistics', 'auditor'] } },
      { key: '/hod/documents', label: 'Document Library', icon: <FileSearchOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/low-stock', label: 'Low Stock', icon: <FallOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/prs', label: 'Purchase Requests', icon: <ProfileOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/requests', label: 'Cross-Site Requests', icon: <SolutionOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      // Bulk Excel import — SME kinds for HOD; inventory/ledger cards appear
      // for admin only (server enforces both). NOT opened to the auditor: it
      // is a write surface end to end.
      { key: '/bulk-import', label: 'Bulk Excel Import', icon: <FileExcelOutlined />, access: w({ anyRole: ['hod'] }) },
    ],
  },
  {
    // The Estimator is a read-only analysis view and /sme/* is likewise
    // `require_level(2)`, so the auditor already reached the data. Its two
    // WRITE surfaces — Master Data and Actual Consumption — are
    // `require_roles("hod")` inside SmePage and on the server, so they simply
    // do not render for an auditor.
    id: 'sme',
    label: 'SME Estimator',
    access: { anyRole: ['hod', 'auditor'] },
    children: [
      { key: '/sme', label: 'Estimator', icon: <ExperimentOutlined />, access: { anyRole: ['hod', 'auditor'] } },
    ],
  },
  {
    id: 'mh',
    label: 'Man-Hours',
    access: { anyRole: ['hod'] },   // Man-Hours exact-locked {hod, admin}
    children: [
      { key: '/manhours', label: 'Labor Tracking', icon: <FieldTimeOutlined />, access: { anyRole: ['hod'] } },
    ],
  },
  {
    id: 'reports',
    label: 'Reports',
    access: { minLevel: 2 },   // {hod, logistics, admin} — SK/supervisor/warehouse excluded
    children: [
      { key: '/reports', label: 'Reports', icon: <BarChartOutlined />, access: { minLevel: 2 } },
    ],
  },
  {
    id: 'logistics',
    label: 'Logistics',
    access: { minLevel: 3 },   // {logistics, admin} (hod level 2 < 3)
    children: [
      { key: '/logistics', label: 'Procurement', icon: <CarOutlined />, access: w({ minLevel: 3 }) },
      // same page as /hod/lining-coverage — distinct route key so the two nav
      // groups never share an antd menu key (admin "All areas" shows both)
      { key: '/logistics/lining-coverage', label: 'Lining Coverage', icon: <ExperimentOutlined />, access: { minLevel: 3 } },
    ],
  },
  {
    id: 'supervisor',
    label: 'Supervisor',
    access: w({ anyRole: ['supervisor'] }),
    children: [
      { key: '/supervisor', label: 'Material Requests', icon: <SolutionOutlined />, access: w({ anyRole: ['supervisor'] }) },
    ],
  },
  {
    id: 'warehouse',
    label: 'Warehouse',
    access: w({ anyRole: ['warehouse_user'] }),   // exact {warehouse_user, admin}
    children: [
      { key: '/warehouse', label: 'Receiving & DN', icon: <InboxOutlined />, access: w({ anyRole: ['warehouse_user'] }), badge: 'warehouse' },
    ],
  },
  {
    id: 'master',
    label: 'Master Data',
    access: w({ minLevel: 3 }),
    children: WRITE_ENTITIES.map((e) => ({
      key: `/master/${e.key}`,
      label: e.label,
      access: w({ minLevel: 3 }) as AccessRule,
    })),
  },
  {
    id: 'admin',
    label: 'Admin',
    access: { minLevel: 4 },
    children: [
      { key: '/admin/users', label: 'Users', icon: <TeamOutlined />, access: w({ minLevel: 4 }) },
      { key: '/admin/pending', label: 'Access Requests', icon: <UserAddOutlined />, access: w({ minLevel: 4 }) },
      { key: '/admin/overdue', label: 'Overdue Actions', icon: <AlertOutlined />, access: { minLevel: 4 }, redBadge: true },
      { key: '/admin/inventory', label: 'Inventory', icon: <DatabaseOutlined />, access: { minLevel: 4 } },
      { key: '/admin/audit', label: 'Audit Log', icon: <FileSearchOutlined />, access: { minLevel: 4 } },
      { key: '/admin/console', label: 'Console', icon: <ControlOutlined />, access: w({ minLevel: 4 }) },
    ],
  },
  {
    id: 'documents',
    label: 'Documents',
    children: [
      { key: '/documents', label: 'Documents', icon: <FileProtectOutlined />, access: { minLevel: 0 } },
    ],
  },
  {
    id: 'account',
    label: 'Account',
    children: [
      { key: '/security', label: 'Security', icon: <SafetyCertificateOutlined />, access: { minLevel: 0 } },
      { key: '/feedback', label: 'Feedback', icon: <MessageOutlined />, access: { minLevel: 0 } },
    ],
  },
]

// Groups an admin sees by DEFAULT (lean console). Operational groups are hidden
// until the admin flips "All areas" — legacy admin-shadow behavior.
export const ADMIN_DEFAULT_GROUPS = new Set([
  'overview', 'records', 'reports', 'master', 'admin', 'documents', 'account',
])

// The group each role works in most — opened by default in the sidebar
// (progressive disclosure: your workspace first, everything else collapsed).
export const PRIMARY_GROUP: Record<string, string> = {
  auditor: 'records',
  store_keeper: 'entry',
  warehouse_user: 'warehouse',
  supervisor: 'supervisor',
  hod: 'hod',
  logistics: 'logistics',
  admin: 'admin',
}

// Where each role lands when it hits a page it cannot see (and the "/" index).
export const ROLE_HOME: Record<string, string> = {
  // An auditor lands on the Dashboard: it is the broadest read-only view, and
  // every write-purpose portal the other roles land on is closed to them.
  auditor: '/',
  store_keeper: '/entry/issue',
  warehouse_user: '/warehouse',
  supervisor: '/supervisor',
  hod: '/hod/approvals',
  logistics: '/logistics',
  admin: '/admin/console',
}

export function roleHome(user: User | null): string {
  if (!user) return '/'
  return ROLE_HOME[user.role] ?? '/'
}

// Route-guard permission: may this user OPEN this page? Admin → always (shadow).
export function canAccess(user: User | null, rule: AccessRule): boolean {
  if (!user) return false
  // Checked BEFORE the admin shadow: a view-only account is never an admin,
  // but putting the test first makes the precedence explicit — `writes` is a
  // capability gate, not another rung on the role ladder, so no amount of
  // seniority opens a page whose only purpose is changing data.
  if (rule.writes && isReadOnly(user)) return false
  if (user.role === 'admin') return true
  if ('anyRole' in rule) return rule.anyRole.includes(user.role)
  return (user.level ?? 0) >= rule.minLevel
}

// Which sidebar group a path belongs to (for keeping the active group open).
export function groupOfPath(pathname: string): string | undefined {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (path.startsWith('/records/')) return 'records'
  if (path.startsWith('/master/')) return 'master'
  for (const g of NAV) {
    if (g.children.some((n) => n.key === path)) return g.id
  }
  return undefined
}

// Flat list of every page this user can OPEN (admin shadow included, ignoring
// the curated-default filter) — powers the ⌘K command palette.
export interface FlatNav { key: string; label: string; group: string }
export function accessibleNodes(user: User | null): FlatNav[] {
  const out: FlatNav[] = []
  for (const g of NAV) {
    if (g.access && !canAccess(user, g.access)) continue
    for (const n of g.children) {
      if (canAccess(user, n.access)) out.push({ key: n.key, label: n.label, group: g.label ?? '' })
    }
  }
  return out
}

// Resolve the access rule for an arbitrary pathname (handles dynamic
// /records/:key and /master/:key). Unknown paths default to allowed.
export function canAccessPath(user: User | null, pathname: string): boolean {
  if (!user) return false
  const path = pathname.replace(/\/+$/, '') || '/'
  if (path.startsWith('/records/')) {
    const key = path.slice('/records/'.length)
    const ent = READ_ENTITIES.find((e: ReadEntity) => e.key === key)
    return ent ? canAccess(user, ent.access) : canAccess(user, { minLevel: 2 })
  }
  if (path.startsWith('/master/')) return canAccess(user, w({ minLevel: 3 }))
  // The QR-scan Material Intelligence page is open to ANY signed-in user:
  // store keepers (level 0) are the ones holding the scanner, and its endpoint
  // is get_current_user + site_scope — not the level-1 gate the /stock LIST
  // carries. Inheriting /stock's rule would bounce an SK off their own scan.
  if (path.startsWith('/stock/material/')) return true
  for (const g of NAV) {
    const node = g.children.find((n) => n.key === path)
    if (node) return canAccess(user, node.access)
  }
  return true // documents/security/feedback and anything unlisted
}
