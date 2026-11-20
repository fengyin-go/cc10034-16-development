import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AttestResult,
  ConfirmFieldResult,
  EntryRow,
  OverviewResult,
  PageResult,
  ReviewAccount,
  ReviewSample,
  ReviewTransaction,
  TransactionReplay,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

async function reviewJson<T>(path: string, init?: RequestInit, fallback = '复核接口请求失败'): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `${fallback}（${response.status}）`)
  }
  return (await response.json()) as T
}

export async function listReviewAccounts(): Promise<{ items: ReviewAccount[]; total: number }> {
  return reviewJson('/api/audit-review/accounts')
}

export async function listReviewTransactions(): Promise<{
  items: ReviewTransaction[]
  total: number
}> {
  return reviewJson('/api/audit-review/transactions')
}

export async function generateReviewSample(txId: string, accountId: string): Promise<ReviewSample> {
  const query = new URLSearchParams({ account_id: accountId }).toString()
  return reviewJson(`/api/audit-review/transactions/${encodeURIComponent(txId)}/samples?${query}`, {
    method: 'POST',
  })
}

export async function getReviewSample(sampleId: string, accountId: string): Promise<ReviewSample> {
  const query = new URLSearchParams({ account_id: accountId }).toString()
  return reviewJson(
    `/api/audit-review/samples/${encodeURIComponent(sampleId)}?${query}`,
    undefined,
    '样本回查失败',
  )
}

export async function confirmReviewField(
  sampleId: string,
  accountId: string,
  memberId: string,
  field: string,
): Promise<ConfirmFieldResult> {
  return reviewJson(
    `/api/audit-review/samples/${encodeURIComponent(sampleId)}/confirmations`,
    {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId, member_id: memberId, field }),
    },
    '字段复核失败',
  )
}

export async function replayTransaction(
  txId: string,
  accountId: string,
): Promise<TransactionReplay> {
  const query = new URLSearchParams({ account_id: accountId }).toString()
  return reviewJson(
    `/api/audit-review/transactions/${encodeURIComponent(txId)}/replay?${query}`,
    { method: 'POST' },
    '补证回放失败',
  )
}

export async function attestTransaction(txId: string, accountId: string): Promise<AttestResult> {
  const query = new URLSearchParams({ account_id: accountId }).toString()
  return reviewJson(
    `/api/audit-review/transactions/${encodeURIComponent(txId)}/attest?${query}`,
    { method: 'POST' },
    '整笔证明失败',
  )
}

export async function revokeReviewGrant(
  accountId: string,
  table: string,
  field?: string,
): Promise<ReviewAccount> {
  return reviewJson(
    `/api/audit-review/accounts/${encodeURIComponent(accountId)}/revoke`,
    { method: 'POST', body: JSON.stringify({ table, field: field || null }) },
    '收回授权失败',
  )
}

export async function grantReviewAccount(
  accountId: string,
  table: string,
  fields: string[],
): Promise<ReviewAccount> {
  return reviewJson(
    `/api/audit-review/accounts/${encodeURIComponent(accountId)}/grants`,
    { method: 'POST', body: JSON.stringify({ table, fields }) },
    '授予授权失败',
  )
}
