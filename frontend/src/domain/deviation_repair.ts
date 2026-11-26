/** 偏差修复确认页的纯业务口径：分组标题、比例格式化、受理勾选与补样输入。 */
import type { ClaimDetail, SampleReceipt, SourceTally } from '@/api/deviation-repair'

export const GROUP_KEYS = ['证据不足来源', '可发起修复来源', '偏差未超阈来源'] as const

export type GroupKey = (typeof GROUP_KEYS)[number]

export const GROUP_HINTS: Record<GroupKey, string> = {
  证据不足来源: '有效样本不足应采数量或存在缺样，不能受理；缺样单列，不按零偏差处理',
  可发起修复来源: '证据充分且偏差比例超过阈值，可按来源勾选确认并写入复验单',
  偏差未超阈来源: '证据充分但偏差未超阈，不需要修复',
}

export function formatRate(value: string | number): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '—'
  return `${(numeric * 100).toFixed(1)}%`
}

export function denominatorText(tally: SourceTally): string {
  return `${tally.偏差数}/${tally.有效样本数}`
}

export function missingText(tally: SourceTally): string {
  const missing = Number(tally.缺样数 || 0)
  if (missing <= 0) return '无缺样'
  return `缺样 ${missing} 份${tally.缺样回执 ? `（${tally.缺样回执}）` : ''}`
}

export function lateSamples(tally: SourceTally): SampleReceipt[] {
  return tally.迟到样本 ?? (tally.样本回执 ?? []).filter((item) => item.到达状态 === '迟到')
}

export function canCompleteLate(tally: SourceTally): boolean {
  return lateSamples(tally).length > 0
}

export function repairableSources(detail: ClaimDetail): SourceTally[] {
  return detail.来源分组['可发起修复来源'] ?? []
}

export function insufficientSources(detail: ClaimDetail): SourceTally[] {
  return detail.来源分组['证据不足来源'] ?? []
}

export function defaultSelection(detail: ClaimDetail): string[] {
  return repairableSources(detail).map((tally) => tally.来源标识)
}

/** 准备阶段可沿迟到样本继续处理的来源：当前已可修复但还没写进复验单。 */
export function lateCandidateSources(detail: ClaimDetail): SourceTally[] {
  const order = detail.复验单
  if (!order || order.复验状态 !== '准备中') return []
  const inScope = new Set([...order.签发范围, ...order.补样范围])
  return repairableSources(detail).filter((tally) => !inScope.has(tally.来源标识))
}

export function canIssue(detail: ClaimDetail): boolean {
  return detail.申请.status === '核对中' || detail.申请.status === '待核对'
}

export function canExecute(detail: ClaimDetail): boolean {
  return Boolean(detail.复验单) && detail.复验单?.复验状态 === '准备中'
}

export function canAcknowledge(detail: ClaimDetail): boolean {
  return detail.申请.status === '已执行'
}

export function batchLabel(detail: ClaimDetail): string {
  return detail.申请.重解析清单编号 || '尚未生成'
}

export function receiptPayload(raw: {
  receiptNo: string
  arrival: string
  deviation: boolean
  conclusion: string
}) {
  return {
    receiptNo: raw.receiptNo.trim(),
    arrival: raw.arrival,
    deviation: raw.deviation,
    conclusion: raw.conclusion.trim(),
  }
}

export function manifestBatches(detail: ClaimDetail): Record<string, unknown>[] {
  const manifest = detail.重解析清单 as
    | { 来源批次?: Record<string, unknown>[] }
    | null
  return manifest?.来源批次 ?? []
}

export function manifestGaps(detail: ClaimDetail): Record<string, unknown>[] {
  const manifest = detail.重解析清单 as
    | { 覆盖缺口?: Record<string, unknown>[] }
    | null
  return manifest?.覆盖缺口 ?? []
}

export function historyNote(detail: ClaimDetail): string {
  if (!detail.复核历史.length) return '尚无历史复核快照'
  const first = detail.复核历史[0]
  return `${detail.复核历史.length} 条快照；首条 ${first.来源标识} 偏差比例 ${formatRate(first.偏差比例)} 按当时 ${first.有效样本数} 份有效样本留存`
}
