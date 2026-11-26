<template>
  <section class="page" data-module="deviation_repair">
    <header class="page-head">
      <div>
        <h2>偏差修复申请</h2>
        <p class="page-desc">
          按来源确认抽样复核的受理范围：每个来源用自己的有效样本作分母，缺样单列不当作零偏差；
          只把确证偏差的来源写进来源复验单，执行后按实际排入的渠道生成按来源重解析批次。
        </p>
      </div>
      <div class="page-actions">
        <select v-model="claimNo" data-testid="claim-select" class="claim-select" @change="reload">
          <option v-for="item in claimOptions" :key="item" :value="item">{{ item }}</option>
        </select>
        <button class="btn" data-testid="reload-detail" type="button" @click="reload">刷新确认页</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="error-text">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="notice-text">{{ notice }}</p>

    <template v-if="detail">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">当前状态</span>
          <strong class="stat-value" data-testid="claim-status">{{ detail.申请.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">可发起修复来源</span>
          <strong class="stat-value" data-testid="repairable-count">{{ repairable.length }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">证据不足来源</span>
          <strong class="stat-value" data-testid="insufficient-count">{{ insufficient.length }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">样本更新提示</span>
          <strong class="stat-value small" data-testid="hint-status">{{ detail.样本更新提示.状态 || '—' }}</strong>
        </article>
      </div>

      <section class="panel">
        <h3>受理范围核对</h3>
        <dl class="kv-grid">
          <div><dt>关联复核</dt><dd>{{ detail.申请.关联复核 }}</dd></div>
          <div><dt>应采样本数</dt><dd>{{ detail.申请.应采样本数 }}</dd></div>
          <div><dt>偏差阈值</dt><dd>{{ formatRate(detail.申请.偏差阈值) }}</dd></div>
          <div><dt>缺样来源</dt><dd data-testid="missing-sources">{{ detail.申请.缺样来源 || '无' }}</dd></div>
          <div><dt>复验单号</dt><dd data-testid="order-no">{{ detail.申请.复验单号 || '未签发' }}</dd></div>
          <div><dt>复验状态</dt><dd data-testid="order-status">{{ detail.复验单?.复验状态 || '—' }}</dd></div>
          <div><dt>实际入队渠道</dt><dd data-testid="queued-channels">{{ detail.申请.实际入队渠道 || '尚未执行' }}</dd></div>
          <div><dt>重解析清单</dt><dd data-testid="manifest-no">{{ batchLabel(detail) }}</dd></div>
        </dl>
        <div class="page-actions">
          <button class="btn" data-testid="verify-scope" type="button" :disabled="busy" @click="onVerify">
            核对受理范围
          </button>
          <button
            class="btn"
            data-testid="confirm-scope"
            type="button"
            :disabled="busy || !canIssue(detail)"
            @click="onConfirm"
          >
            按来源确认受理
          </button>
          <button
            class="btn primary"
            data-testid="issue-recheck"
            type="button"
            :disabled="busy || !canIssue(detail)"
            @click="onIssue"
          >
            签发来源复验单
          </button>
          <button
            class="btn primary"
            data-testid="execute-recheck"
            type="button"
            :disabled="busy || !canExecute(detail)"
            @click="onExecute"
          >
            执行复验单
          </button>
          <button
            class="btn"
            data-testid="acknowledge"
            type="button"
            :disabled="busy || !canAcknowledge(detail)"
            @click="onAcknowledge"
          >
            确认回填
          </button>
        </div>
      </section>

      <section
        v-for="group in GROUP_KEYS"
        :key="group"
        class="panel group-panel"
        :data-testid="`group-${group}`"
      >
        <header class="group-head">
          <h3>{{ group }}（{{ (detail.来源分组[group] || []).length }}）</h3>
          <p class="group-hint">{{ GROUP_HINTS[group] }}</p>
        </header>
        <table class="data-table">
          <thead>
            <tr>
              <th v-if="group === '可发起修复来源'">选择</th>
              <th>来源</th>
              <th>渠道</th>
              <th>分母口径</th>
              <th>有效样本</th>
              <th>偏差</th>
              <th>偏差比例</th>
              <th>阈值</th>
              <th>缺样</th>
              <th>受理结论</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="tally in detail.来源分组[group] || []"
              :key="tally.来源标识"
              :data-testid="`source-${tally.来源标识}`"
            >
              <td v-if="group === '可发起修复来源'">
                <input
                  v-model="selectedSources"
                  type="checkbox"
                  :value="tally.来源标识"
                  :data-testid="`select-${tally.来源标识}`"
                />
              </td>
              <td>{{ tally.来源标识 }}</td>
              <td>{{ tally.来源渠道 || '—' }}</td>
              <td>{{ tally.分母口径 }}</td>
              <td :data-testid="`valid-${tally.来源标识}`">{{ tally.有效样本数 }}</td>
              <td :data-testid="`deviation-${tally.来源标识}`">{{ tally.偏差数 }}</td>
              <td>{{ formatRate(tally.偏差比例) }}</td>
              <td>{{ formatRate(tally.偏差阈值) }}</td>
              <td :data-testid="`missing-${tally.来源标识}`">{{ missingText(tally) }}</td>
              <td>{{ tally.受理结论 }}</td>
              <td class="row-actions">
                <button
                  v-if="canCompleteLate(tally)"
                  class="link"
                  type="button"
                  :data-testid="`complete-late-${tally.来源标识}`"
                  :disabled="busy"
                  @click="onCompleteLate(tally.来源标识)"
                >
                  补齐迟到样本
                </button>
                <button
                  v-if="isLateCandidate(tally)"
                  class="link"
                  type="button"
                  :data-testid="`include-late-${tally.来源标识}`"
                  :disabled="busy"
                  @click="onIncludeLate(tally.来源标识)"
                >
                  沿迟到样本追加
                </button>
                <button
                  class="link"
                  type="button"
                  :data-testid="`add-receipt-${tally.来源标识}`"
                  @click="openReceipt(tally)"
                >
                  登记样本回执
                </button>
              </td>
            </tr>
            <tr v-if="!(detail.来源分组[group] || []).length">
              <td :colspan="group === '可发起修复来源' ? 11 : 10" class="empty-state">
                该分组暂无来源
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel" data-testid="sample-update-hint">
        <h3>样本更新提示</h3>
        <p>{{ detail.样本更新提示.提示 || '暂无提示' }}</p>
        <button
          class="btn"
          data-testid="handle-sample-update"
          type="button"
          :disabled="busy || !detail.样本更新提示.可处理"
          @click="onHandleSampleUpdate"
        >
          处理样本更新
        </button>
      </section>

      <section v-if="detail.复验单" class="panel" data-testid="order-panel">
        <h3>来源复验单 {{ detail.复验单.复验单号 }}</h3>
        <dl class="kv-grid">
          <div><dt>签发范围</dt><dd data-testid="issued-scope">{{ detail.复验单.签发范围.join(',') || '无' }}</dd></div>
          <div><dt>补样范围</dt><dd data-testid="late-scope">{{ detail.复验单.补样范围.join(',') || '无' }}</dd></div>
          <div><dt>覆盖策略</dt><dd>{{ detail.复验单.覆盖策略 }}</dd></div>
          <div><dt>签发人</dt><dd>{{ detail.复验单.签发人标识 }}</dd></div>
          <div><dt>复核依据</dt><dd data-testid="execution-basis">{{ detail.复验单.执行结果?.复核依据 || '尚未执行' }}</dd></div>
        </dl>
        <div v-if="detail.复验单.执行结果" data-testid="execution-result">
          <table class="data-table">
            <thead>
              <tr><th>入队来源</th><th>未入队来源</th><th>实际入队渠道</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>{{ detail.复验单.执行结果.入队来源.join(',') || '无' }}</td>
                <td>
                  <span v-if="!detail.复验单.执行结果.未入队来源.length">无</span>
                  <span v-for="item in detail.复验单.执行结果.未入队来源" :key="item.来源标识">
                    {{ item.来源标识 }}（{{ item.原因 }}）
                  </span>
                </td>
                <td>{{ detail.复验单.执行结果.实际入队渠道.join(',') || '无' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ol class="audit-list">
          <li v-for="item in detail.复验单.动作留痕" :key="`${item.时间}-${item.动作}`">
            {{ item.时间 }} · {{ item.动作 }} · {{ item.来源标识 }} · {{ item.证据 }}
          </li>
        </ol>
      </section>

      <section v-if="detail.重解析清单" class="panel" data-testid="manifest-panel">
        <h3>按来源重解析清单</h3>
        <p data-testid="manifest-scope">{{ detail.重解析清单.重解析范围 }}</p>
        <table class="data-table">
          <thead>
            <tr><th>批次标识</th><th>来源</th><th>渠道</th><th>偏差</th><th>阈值</th><th>重解析范围</th></tr>
          </thead>
          <tbody>
            <tr v-for="batch in manifestBatches(detail)" :key="String(batch.批次标识)">
              <td>{{ batch.批次标识 }}</td>
              <td>{{ batch.来源标识 }}</td>
              <td>{{ batch.来源渠道 }}</td>
              <td>{{ batch.偏差数 }}/{{ batch.有效样本数 }}</td>
              <td>{{ formatRate(String(batch.偏差阈值)) }}</td>
              <td>{{ batch.重解析范围 }}</td>
            </tr>
          </tbody>
        </table>
        <h4>覆盖缺口核对</h4>
        <ul class="gap-list">
          <li v-for="gap in manifestGaps(detail)" :key="String(gap.来源标识)">
            {{ gap.来源标识 }} · 纳入重解析 {{ gap.纳入重解析 ? '是' : '否' }} · {{ gap.覆盖缺口 }}
          </li>
        </ul>
      </section>

      <section class="panel" data-testid="history-panel">
        <h3>历史复核</h3>
        <p class="group-hint">{{ historyNote(detail) }}</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>序号</th><th>时间</th><th>动作</th><th>来源</th><th>应采</th>
              <th>有效样本</th><th>偏差</th><th>偏差比例</th><th>阈值</th><th>受理结论</th><th>当时原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in detail.复核历史" :key="item.序号" :data-testid="`history-${item.序号}`">
              <td>{{ item.序号 }}</td>
              <td>{{ item.时间 }}</td>
              <td>{{ item.动作 }}</td>
              <td>{{ item.来源标识 }}</td>
              <td>{{ item.应采样本数 }}</td>
              <td>{{ item.有效样本数 }}</td>
              <td>{{ item.偏差数 }}</td>
              <td>{{ formatRate(item.偏差比例) }}</td>
              <td>{{ formatRate(item.偏差阈值) }}</td>
              <td>{{ item.受理结论 }}</td>
              <td :data-testid="`history-reason-${item.序号}`">{{ item.不可受理原因 || '—' }}</td>
            </tr>
            <tr v-if="!detail.复核历史.length">
              <td colspan="11" class="empty-state">尚无历史复核快照</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <div v-if="receiptTarget" class="modal-backdrop" data-testid="receipt-modal">
      <form class="modal" @submit.prevent="submitReceipt">
        <h3>登记样本回执 · {{ receiptTarget.来源标识 }}</h3>
        <label class="create-field">
          <span>回执号（相同回执号只算一次）</span>
          <input v-model="receiptForm.receiptNo" data-testid="receipt-no" required />
        </label>
        <label class="create-field">
          <span>到达状态</span>
          <select v-model="receiptForm.arrival" data-testid="receipt-arrival">
            <option value="已到">已到</option>
            <option value="迟到">迟到</option>
          </select>
        </label>
        <label class="create-field inline">
          <input v-model="receiptForm.deviation" type="checkbox" data-testid="receipt-deviation" />
          <span>复核结果为偏差</span>
        </label>
        <label class="create-field">
          <span>复核结论</span>
          <input v-model="receiptForm.conclusion" data-testid="receipt-conclusion" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="receiptTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-receipt" type="submit" :disabled="busy">
            提交回执
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  acknowledgeClaim,
  completeLateSamples,
  confirmScope,
  executeRecheck,
  handleSampleUpdate,
  includeLateSource,
  issueRecheck,
  loadClaimDetail,
  registerSamples,
  verifyScope,
} from '@/api/deviation-repair'
import type { ClaimDetail, SourceTally } from '@/api/deviation-repair'
import { fetchJson } from '@/api/client'
import {
  GROUP_HINTS,
  GROUP_KEYS,
  batchLabel,
  canAcknowledge,
  canCompleteLate,
  canExecute,
  canIssue,
  formatRate,
  historyNote,
  insufficientSources,
  lateCandidateSources,
  manifestBatches,
  manifestGaps,
  missingText,
  repairableSources,
} from '@/domain/deviation_repair'

const claimNo = ref('DRP-001')
const claimOptions = ref<string[]>(['DRP-001'])
const detail = ref<ClaimDetail | null>(null)
const selectedSources = ref<string[]>([])
const errorMessage = ref('')
const notice = ref('')
const busy = ref(false)
const receiptTarget = ref<SourceTally | null>(null)
const receiptForm = ref({
  receiptNo: '',
  arrival: '已到',
  deviation: false,
  conclusion: '',
})

const repairable = computed(() => (detail.value ? repairableSources(detail.value) : []))
const insufficient = computed(() => (detail.value ? insufficientSources(detail.value) : []))
const lateCandidates = computed(() => (detail.value ? lateCandidateSources(detail.value) : []))

function isLateCandidate(tally: SourceTally): boolean {
  return lateCandidates.value.some((item) => item.来源标识 === tally.来源标识)
}

function nowText(): string {
  return new Date().toISOString().slice(0, 19)
}

async function loadClaimOptions() {
  try {
    const payload = await fetchJson<{ items: { 申请编号: string }[] }>(
      '/api/deviation_repair?size=200',
    )
    const options = payload.items.map((item) => item.申请编号).filter(Boolean)
    if (options.length) {
      claimOptions.value = options
      if (!options.includes(claimNo.value)) claimNo.value = options[0]
    }
  } catch {
    claimOptions.value = [claimNo.value]
  }
}

async function reload() {
  errorMessage.value = ''
  busy.value = true
  try {
    detail.value = await loadClaimDetail(claimNo.value)
    // 复核员已确认过受理范围时沿用确认结果，不能被刷新重新全选。
    const confirmed = String(detail.value.申请.确认受理来源 || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const available = new Set(repairable.value.map((tally) => tally.来源标识))
    selectedSources.value = confirmed.length
      ? confirmed.filter((source) => available.has(source))
      : [...available]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认页读取失败'
  } finally {
    busy.value = false
  }
}

async function runAction(action: () => Promise<{ ok: boolean; message: string }>) {
  errorMessage.value = ''
  notice.value = ''
  busy.value = true
  try {
    const result = await action()
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

function onVerify() {
  return runAction(() => verifyScope(claimNo.value, nowText()))
}

function onConfirm() {
  return runAction(() => confirmScope(claimNo.value, selectedSources.value, nowText()))
}

async function onIssue() {
  // 签发范围以复核员在确认页勾选的来源为准；还没确认过就先落一次确认。
  errorMessage.value = ''
  notice.value = ''
  busy.value = true
  try {
    const sources = [...selectedSources.value]
    const confirmed = await confirmScope(claimNo.value, sources, nowText())
    if (!confirmed.ok) throw new Error(confirmed.message)
    const result = await issueRecheck(
      claimNo.value, sources, '复核员-01', '旧投递保留', nowText())
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

function onExecute() {
  return runAction(() => executeRecheck(claimNo.value, nowText()))
}

function onAcknowledge() {
  return runAction(() => acknowledgeClaim(claimNo.value, nowText()))
}

function onHandleSampleUpdate() {
  return runAction(() => handleSampleUpdate(claimNo.value, nowText()))
}

function onCompleteLate(source: string) {
  return runAction(() => completeLateSamples(claimNo.value, source, nowText()))
}

function onIncludeLate(source: string) {
  return runAction(() => includeLateSource(claimNo.value, source, nowText()))
}

function openReceipt(tally: SourceTally) {
  receiptTarget.value = tally
  receiptForm.value = { receiptNo: '', arrival: '已到', deviation: false, conclusion: '' }
}

async function submitReceipt() {
  if (!receiptTarget.value) return
  const source = receiptTarget.value.来源标识
  const payload = {
    receiptNo: receiptForm.value.receiptNo,
    arrival: receiptForm.value.arrival,
    deviation: receiptForm.value.deviation,
    conclusion: receiptForm.value.conclusion,
  }
  receiptTarget.value = null
  await runAction(() =>
    registerSamples(claimNo.value, source, [payload], nowText()),
  )
}

onMounted(async () => {
  await loadClaimOptions()
  await reload()
})
</script>

<style scoped>
.claim-select { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 8px; font-size: 15px; }
.panel h4 { margin: 12px 0 6px; font-size: 13px; }
.group-head { margin-bottom: 8px; }
.group-hint { color: var(--muted); font-size: 12px; margin: 4px 0 0; }
.kv-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 12px; margin: 0 0 10px; }
.kv-grid dt { font-size: 12px; color: var(--muted); }
.kv-grid dd { margin: 2px 0 0; font-size: 13px; }
.stat-value.small { font-size: 15px; }
.notice-text { color: #0f5132; background: #e8f5ec; border: 1px solid #b7e0c4; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.audit-list { margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--muted); }
.gap-list { margin: 0; padding-left: 18px; font-size: 13px; }
.create-field.inline { display: flex; align-items: center; gap: 8px; }
.create-field select { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
</style>
