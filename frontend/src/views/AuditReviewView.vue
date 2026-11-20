<template>
  <section class="page" data-module="audit_review">
    <header class="page-head">
      <div>
        <h2>审计复核台</h2>
        <p class="page-desc">
          把变更事件的事务镜像按查阅权限转成脱敏复核样本；生成样本和回查旧样本都会重新判定字段权限。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>复核账号</span>
          <select v-model="accountId" data-testid="review-account" @change="onAccountChange">
            <option v-for="account in accounts" :key="account.account_id" :value="account.account_id">
              {{ account.name }}（{{ account.account_id }}）
            </option>
          </select>
        </label>
        <button class="btn" data-testid="refresh-transactions" type="button" @click="loadTransactions">
          刷新事务
        </button>
      </div>
    </header>

    <p class="status-legend">{{ accountHint }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="review-error">{{ errorMessage }}</p>
    <p v-if="notice" class="page-desc" data-testid="review-notice">{{ notice }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>事务ID</th>
          <th>成员数</th>
          <th>涉及表</th>
          <th>实际变更字段数</th>
          <th>事件时间</th>
          <th>样本数</th>
          <th>整笔已确认</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="tx in transactions" :key="tx.tx_id" :data-testid="`tx-${tx.tx_id}`">
          <td>{{ tx.tx_id }}</td>
          <td>{{ tx.member_count }}</td>
          <td>{{ tx.tables.join('、') }}</td>
          <td>{{ tx.changed_field_count }}</td>
          <td>{{ tx.event_time }}</td>
          <td>{{ tx.sample_count }}</td>
          <td>{{ tx.attested ? '是' : '否' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`generate-${tx.tx_id}`" @click="generate(tx.tx_id)">
              生成样本
            </button>
            <button class="link" type="button" :data-testid="`replay-${tx.tx_id}`" @click="replay(tx.tx_id)">
              补证回放
            </button>
            <button class="link" type="button" :data-testid="`attest-${tx.tx_id}`" @click="attest(tx.tx_id)">
              出具整笔证明
            </button>
          </td>
        </tr>
        <tr v-if="!transactions.length">
          <td colspan="8" class="empty-state">暂无可复核的跨表事务</td>
        </tr>
      </tbody>
    </table>

    <article v-if="sample" class="review-panel" data-testid="sample-panel">
      <header class="page-head">
        <div>
          <h3>样本 {{ sample.sample_id }}</h3>
          <p class="page-desc">
            生成人 {{ sample.generated_by }}｜当前查阅账号 {{ sample.account_id }}｜
            可见字段 {{ sample.visible_field_count }} 个，脱敏字段 {{ sample.masked_field_count }} 个
          </p>
        </div>
        <button class="btn" type="button" data-testid="reload-sample" @click="reloadSample">
          回查旧样本
        </button>
      </header>
      <p class="page-desc">{{ sample.notice }}</p>

      <div v-for="member in sample.members" :key="member.member_id" class="review-member" :data-testid="`member-${member.member_id}`">
        <h4>{{ member.table }} · {{ member.operation }} · {{ member.member_id }}</h4>
        <p class="page-desc">{{ member.reason }}</p>
        <table class="data-table">
          <thead>
            <tr><th>字段</th><th>变更前</th><th>变更后</th><th>复核状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="field in member.fields" :key="field.field" :data-testid="`field-${member.member_id}-${field.field}`">
              <td>{{ field.field }}</td>
              <td>{{ field.before ?? '—' }}</td>
              <td>{{ field.after ?? '—' }}</td>
              <td>{{ member.confirmed_fields.includes(field.field) ? '已确认' : '待复核' }}</td>
              <td>
                <button
                  v-if="!member.confirmed_fields.includes(field.field)"
                  class="link"
                  type="button"
                  :data-testid="`confirm-${member.member_id}-${field.field}`"
                  @click="confirm(member.member_id, field.field)"
                >
                  确认一致
                </button>
                <span v-else>—</span>
              </td>
            </tr>
            <tr v-if="!member.fields.length">
              <td colspan="5" class="empty-state">该成员字段均超出当前账号查阅范围，已全部脱敏</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h4>已确认范围（{{ sample.confirmed_field_count }} 个字段）</h4>
      <table class="data-table">
        <thead>
          <tr><th>成员</th><th>表</th><th>字段</th><th>变更前</th><th>变更后</th><th>确认人</th><th>确认时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in sample.confirmed_scope" :key="`${item.member_id}-${item.field}`">
            <td>{{ item.member_id }}</td>
            <td>{{ item.table }}</td>
            <td>{{ item.field }}</td>
            <td>{{ item.before ?? '—' }}</td>
            <td>{{ item.after ?? '—' }}</td>
            <td>{{ item.confirmed_by }}</td>
            <td>{{ item.confirmed_at }}</td>
          </tr>
          <tr v-if="!sample.confirmed_scope.length">
            <td colspan="7" class="empty-state">当前账号尚无可见的有效确认</td>
          </tr>
        </tbody>
      </table>

      <p class="page-desc" data-testid="sample-supplement">
        本轮仍需补证的成员（{{ sample.members_to_supplement.length }}）：
        <strong>{{ sample.members_to_supplement.join('、') || '无' }}</strong>
      </p>
      <p class="page-desc" data-testid="attestation-state">
        整笔证明：{{ sample.attestation.attested ? '已出具' : '未出具' }}｜{{ sample.attestation.reason }}
      </p>
    </article>

    <article v-if="replayResult" class="review-panel" data-testid="replay-panel">
      <h3>补证回放 · {{ replayResult.tx_id }}</h3>
      <p class="page-desc">{{ replayResult.notice }}</p>
      <p>
        本轮仍需补证的成员（{{ replayResult.supplement_member_count }}）：
        <strong data-testid="supplement-members">
          {{ replayResult.members_to_supplement.join('、') || '无' }}
        </strong>
      </p>
      <p>
        当前账号可见的已确认范围：<strong data-testid="replay-confirmed-count">{{ replayResult.confirmed_field_count }}</strong>
        个字段
      </p>
      <p class="page-desc">{{ replayResult.attestation.reason }}</p>
    </article>

    <article class="review-panel" data-testid="grant-panel">
      <h3>授权操作</h3>
      <p class="page-desc">
        授权直接决定样本可见字段与确认是否有效；收回权限后旧样本回查会重新脱敏，历史确认也不再有效。
      </p>
      <form class="filter-bar" @submit.prevent="revoke">
        <label class="filter-item">
          <span>收回表权限</span>
          <input v-model="revokeTable" data-testid="revoke-table" placeholder="sales.orders" required />
        </label>
        <label class="filter-item">
          <span>字段（留空收回整表）</span>
          <input v-model="revokeField" data-testid="revoke-field" placeholder="status" />
        </label>
        <button class="btn" data-testid="revoke-submit" type="submit">收回授权</button>
      </form>
      <form class="filter-bar" @submit.prevent="restore">
        <label class="filter-item">
          <span>恢复表权限</span>
          <input v-model="grantTable" data-testid="grant-table" placeholder="sales.orders" required />
        </label>
        <label class="filter-item">
          <span>字段（逗号分隔，* 表示全部）</span>
          <input v-model="grantFields" data-testid="grant-fields" placeholder="status" required />
        </label>
        <button class="btn primary" data-testid="grant-submit" type="submit">授予授权</button>
      </form>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  attestTransaction,
  confirmReviewField,
  generateReviewSample,
  getReviewSample,
  grantReviewAccount,
  listReviewAccounts,
  listReviewTransactions,
  replayTransaction,
  revokeReviewGrant,
} from '@/api/backend-service'
import type {
  AttestResult,
  ReviewAccount,
  ReviewSample,
  ReviewTransaction,
  TransactionReplay,
} from '@/data/types'

const accounts = ref<ReviewAccount[]>([])
const accountId = ref('order_status_reader')
const transactions = ref<ReviewTransaction[]>([])
const sample = ref<ReviewSample | null>(null)
const replayResult = ref<TransactionReplay | null>(null)
const errorMessage = ref('')
const notice = ref('')
const revokeTable = ref('sales.orders')
const revokeField = ref('status')
const grantTable = ref('sales.orders')
const grantFields = ref('status')

const accountHint = computed(() => {
  const account = accounts.value.find((item) => item.account_id === accountId.value)
  if (!account) return '尚未选择复核账号'
  const grants = account.grants
    .map((grant) => {
      const denied = grant.denied_fields.length ? `，已收回 ${grant.denied_fields.join('/')}` : ''
      return `${grant.table}:${grant.fields.join('/') || '无'}${denied}${grant.active ? '' : '（整表已收回）'}`
    })
    .join('；')
  return `${account.description}｜${grants || '无有效授权'}`
})

function fail(error: unknown) {
  errorMessage.value = error instanceof Error ? error.message : '复核操作失败'
}

async function loadAccounts() {
  try {
    const payload = await listReviewAccounts()
    accounts.value = payload.items
    if (!accounts.value.some((item) => item.account_id === accountId.value) && accounts.value.length) {
      accountId.value = accounts.value[0].account_id
    }
  } catch (error) {
    fail(error)
  }
}

async function loadTransactions() {
  errorMessage.value = ''
  try {
    const payload = await listReviewTransactions()
    transactions.value = payload.items
  } catch (error) {
    fail(error)
  }
}

async function onAccountChange() {
  sample.value = null
  replayResult.value = null
  notice.value = ''
  await loadTransactions()
}

async function generate(txId: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    sample.value = await generateReviewSample(txId, accountId.value)
    replayResult.value = null
    await loadTransactions()
  } catch (error) {
    fail(error)
  }
}

async function reloadSample() {
  if (!sample.value) return
  errorMessage.value = ''
  try {
    sample.value = await getReviewSample(sample.value.sample_id, accountId.value)
    notice.value = '已按当前授权重新判定旧样本，越权字段不再显示。'
  } catch (error) {
    fail(error)
  }
}

async function confirm(memberId: string, field: string) {
  if (!sample.value) return
  errorMessage.value = ''
  try {
    const result = await confirmReviewField(
      sample.value.sample_id,
      accountId.value,
      memberId,
      field,
    )
    sample.value = result.sample
    notice.value = result.message
  } catch (error) {
    fail(error)
  }
}

async function replay(txId: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    replayResult.value = await replayTransaction(txId, accountId.value)
    if (sample.value?.tx_id === txId) {
      sample.value = await getReviewSample(sample.value.sample_id, accountId.value)
    }
    await loadTransactions()
  } catch (error) {
    fail(error)
  }
}

async function attest(txId: string) {
  errorMessage.value = ''
  try {
    const result: AttestResult = await attestTransaction(txId, accountId.value)
    notice.value = result.attested ? result.message : `未出具：${result.message}`
    if (sample.value?.tx_id === txId) {
      sample.value = await getReviewSample(sample.value.sample_id, accountId.value)
    }
    await loadTransactions()
  } catch (error) {
    fail(error)
  }
}

async function revoke() {
  errorMessage.value = ''
  notice.value = ''
  try {
    await revokeReviewGrant(accountId.value, revokeTable.value, revokeField.value || undefined)
    notice.value = `已收回 ${accountId.value} 的 ${revokeTable.value}${revokeField.value ? '.' + revokeField.value : ''} 查阅权限。`
    await loadAccounts()
    if (sample.value) await reloadSample()
    await loadTransactions()
  } catch (error) {
    fail(error)
  }
}

async function restore() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const fields = grantFields.value.split(',').map((item) => item.trim()).filter(Boolean)
    await grantReviewAccount(accountId.value, grantTable.value, fields)
    notice.value = `已恢复 ${accountId.value} 的 ${grantTable.value} 字段授权。`
    await loadAccounts()
  } catch (error) {
    fail(error)
  }
}

onMounted(async () => {
  await loadAccounts()
  await loadTransactions()
})
</script>

<style scoped>
.review-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}
.review-member { margin: 12px 0; }
.review-member h4 { margin: 8px 0 4px; font-size: 14px; }
</style>
