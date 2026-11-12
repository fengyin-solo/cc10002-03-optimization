/** 聚合查询跨模块依赖：候选选择、校验预览、重复口径与报告依赖解释的接口封装。 */
import { request } from './client'

const AGG = '/api/aggregation'
const REPORT = '/api/report'

export interface Candidate {
  id: number
  code: string
  name: string
  version?: string
  keyColumn?: string
  sourceTable?: string
  formula?: string
  status: string
  usable: boolean
  reason: string
}

export interface CandidateResponse {
  metrics: Candidate[]
  dimensions: Candidate[]
  snapshotRevisions: Record<string, number>
}

export type RefStatus = 'resolved' | 'ambiguous' | 'not_found' | 'unusable'

export interface ResolvedRef {
  token: string
  kind: 'metric' | 'dimension' | 'aggregation'
  status: RefStatus
  usable: boolean
  reason: string
  candidates: Candidate[]
  selected: Candidate | null
  preview?: PreviewResponse
}

export interface SnapshotInfo {
  revisions: Record<string, number>
  fingerprint: string
  metrics: Candidate[]
  dimensions: Candidate[]
}

export interface PreviewResponse {
  ok: boolean
  fingerprint: string
  snapshot: SnapshotInfo
  metricRefs: ResolvedRef[]
  dimensionRefs: ResolvedRef[]
  errors: string[]
  cache: { preview: 'hit' | 'miss'; fingerprint: string }
}

export interface DuplicateGroup {
  key: string
  count: number
  rules: { id: number; code: string; status: string }[]
}

export interface ReportDependency {
  ok: boolean
  ruleTokens: string[]
  ruleRefs: ResolvedRef[]
  errors: string[]
  adopted: { ruleCode: string; snapshot: SnapshotInfo }[]
  snapshotRevisions: Record<string, number>
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload?.detail ? String(payload.detail) : `接口返回 ${response.status}`)
  }
  return payload as T
}

export function fetchCandidates(): Promise<CandidateResponse> {
  return request(`${AGG}/candidates`).then(readJson<CandidateResponse>)
}

export function previewAggregation(values: Record<string, unknown>): Promise<PreviewResponse> {
  return request(`${AGG}/preview`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  }).then(readJson<PreviewResponse>)
}

export function saveAggregationRule(entryId: number | null, values: Record<string, unknown>) {
  const body = JSON.stringify({ values })
  const path = entryId ? `${AGG}/${entryId}` : AGG
  return request(path, { method: entryId ? 'PUT' : 'POST', body }).then(readJson<{ ok: boolean; message: string }>)
}

export function fetchDuplicates(): Promise<{ groups: DuplicateGroup[]; total: number }> {
  return request(`${AGG}/duplicates`).then(readJson<{ groups: DuplicateGroup[]; total: number }>)
}

export function previewReportDependency(ruleText: string): Promise<ReportDependency> {
  return request(`${REPORT}/dependency-preview`, {
    method: 'POST',
    body: JSON.stringify({ values: { 聚合规则: ruleText } }),
  }).then(readJson<ReportDependency>)
}
