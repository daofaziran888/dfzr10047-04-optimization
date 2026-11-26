/** 偏差修复接口：确认页分组核对、补样、签发与执行来源复验单。 */
import { request } from './client'

export type SourceTally = {
  来源标识: string
  来源渠道: string
  应采样本数: string
  偏差阈值: string
  有效样本数: string
  一致数: string
  偏差数: string
  偏差比例: string
  分母口径: string
  使用样本回执: string
  偏差样本: string
  缺样回执: string
  缺样数: string
  重复回执: string
  证据充分: boolean
  确证偏差: boolean
  受理结论: string
  不可受理原因: string
  受理说明: string
  关联投递批次?: string
  样本回执?: SampleReceipt[]
  迟到样本?: SampleReceipt[]
}

export type SampleReceipt = {
  样本回执号: string
  到达状态: string
  偏差标记: string
  复核结论: string
  登记时间: string
}

export type ClaimSummary = {
  申请编号: string
  关联复核: string
  应采样本数: string
  偏差阈值: string
  来源清单: string
  可修复来源: string
  证据不足来源: string
  偏差未超阈来源: string
  缺样来源: string
  复验单号: string
  复验状态: string
  实际入队渠道: string
  重解析清单编号: string
  确认受理来源: string
  样本更新提示: string
  提示状态: string
  status: string
  pending: boolean
  abnormal: boolean
}

export type OrderView = {
  复验单号: string
  关联申请: string
  覆盖策略: string
  复验状态: string
  签发人标识: string
  签发时间: string
  签发范围: string[]
  补样范围: string[]
  执行结果: {
    执行时间: string
    入队来源: string[]
    未入队来源: { 来源标识: string; 原因: string }[]
    实际入队渠道: string[]
    复核依据: string
  } | null
  动作留痕: { 时间: string; 动作: string; 来源标识: string; 证据: string; 受理结论: string }[]
}

export type HistoryEntry = {
  序号: number
  时间: string
  动作: string
  来源标识: string
  应采样本数: string
  偏差阈值: string
  有效样本数: string
  偏差数: string
  偏差比例: string
  使用样本回执: string
  缺样回执: string
  重复回执: string
  受理结论: string
  不可受理原因: string
  说明: string
}

export type ClaimDetail = {
  申请: ClaimSummary
  来源分组: Record<string, SourceTally[]>
  来源明细: SourceTally[]
  复核历史: HistoryEntry[]
  复验单: OrderView | null
  重解析清单: Record<string, unknown> | null
  样本更新提示: { 提示: string; 状态: string; 可处理: boolean }
}

type ActionResult<T = unknown> = { ok: boolean; message: string; entry: T | null }

async function post<T>(path: string, body: Record<string, unknown>): Promise<ActionResult<T>> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，数据未更新`)
  return (await response.json()) as ActionResult<T>
}

export async function loadClaimDetail(claimNo: string): Promise<ClaimDetail> {
  const response = await request(`/api/deviation_repair/${claimNo}/detail`)
  if (!response.ok) throw new Error(`接口返回 ${response.status}，确认页读取失败`)
  return (await response.json()) as ClaimDetail
}

export async function loadClaimHistory(claimNo: string): Promise<HistoryEntry[]> {
  const response = await request(`/api/deviation_repair/${claimNo}/history`)
  if (!response.ok) throw new Error(`接口返回 ${response.status}，历史复核读取失败`)
  const payload = (await response.json()) as { items: HistoryEntry[] }
  return payload.items
}

export function verifyScope(claimNo: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/verify`, { at })
}

export function registerSamples(
  claimNo: string,
  source: string,
  receipts: { receiptNo: string; arrival: string; deviation: boolean; conclusion: string }[],
  at: string,
) {
  return post(`/api/deviation_repair/${claimNo}/samples`, { at, source, receipts })
}

export function completeLateSamples(claimNo: string, source: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/late-samples`, { at, source })
}

export function confirmScope(claimNo: string, sources: string[], at: string) {
  return post(`/api/deviation_repair/${claimNo}/confirm`, { at, sources })
}

export function issueRecheck(
  claimNo: string,
  sources: string[],
  operator: string,
  coverageStrategy: string,
  at: string,
) {
  return post(`/api/deviation_repair/${claimNo}/recheck`, {
    at, sources, operator, coverage_strategy: coverageStrategy,
  })
}

export function includeLateSource(claimNo: string, source: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/late-source`, { at, source })
}

export function executeRecheck(claimNo: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/execute`, { at })
}

export function handleSampleUpdate(claimNo: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/sample-update`, { at })
}

export function acknowledgeClaim(claimNo: string, at: string) {
  return post(`/api/deviation_repair/${claimNo}/acknowledge`, { at })
}
