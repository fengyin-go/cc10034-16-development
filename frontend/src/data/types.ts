/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type ReviewGrant = {
  table: string
  fields: string[]
  denied_fields: string[]
  active: boolean
}

export type ReviewAccount = {
  account_id: string
  name: string
  description: string
  read_only: boolean
  grants: ReviewGrant[]
}

export type ReviewTransaction = {
  tx_id: string
  member_count: number
  tables: string[]
  changed_field_count: number
  event_time: string
  sample_count: number
  attested: boolean
}

export type ReviewField = {
  field: string
  before: unknown
  after: unknown
  changed: boolean
}

export type ReviewMember = {
  member_id: string
  event_id: string
  table: string
  operation: string
  event_time: string
  fields: ReviewField[]
  visible_field_count: number
  masked_field_count: number
  required_field_count: number
  confirmed_fields: string[]
  missing_fields: string[]
  needs_evidence: boolean
  reason: string
}

export type ConfirmedScopeItem = {
  member_id: string
  table: string
  field: string
  before: unknown
  after: unknown
  decision: string
  confirmed_by: string
  confirmed_at: string
}

export type ReviewAttestation = {
  attested: boolean
  reason: string
  required_field_count?: number
  confirmed_field_count?: number
  missing_field_count?: number
  pending_member_count?: number
}

export type ReviewSample = {
  sample_id: string
  tx_id: string
  account_id: string
  generated_by: string
  generated_at: string
  members: ReviewMember[]
  member_count: number
  visible_field_count: number
  masked_field_count: number
  confirmed_scope: ConfirmedScopeItem[]
  confirmed_field_count: number
  members_to_supplement: string[]
  attested: boolean
  attestation: ReviewAttestation
  notice: string
}

export type TransactionReplay = {
  tx_id: string
  account_id: string
  generated_at: string
  members: ReviewMember[]
  members_to_supplement: string[]
  supplement_member_count: number
  confirmed_scope: ConfirmedScopeItem[]
  confirmed_field_count: number
  attested: boolean
  attestation: ReviewAttestation
  notice: string
}

export type ConfirmFieldResult = {
  ok: boolean
  message: string
  sample: ReviewSample
}

export type AttestResult = {
  ok: boolean
  attested: boolean
  message: string
  attestation: ReviewAttestation
  confirmed_scope: ConfirmedScopeItem[]
}
