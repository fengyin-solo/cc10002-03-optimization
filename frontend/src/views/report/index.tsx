import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { ReportDependency, previewReportDependency } from '@/api/dependencies'

const ENDPOINT = '/api/report'
const columns = [
  '模板编号',
  '模板名称',
  '包含指标',
  '聚合规则',
  '报告周期',
  '图表模板',
  '导出格式',
  '推送渠道',
  '接收人列表',
]
const actions = [
  '提交审核',
  '生效发布',
  '停用模板',
]

type Row = Record<string, string | number | null>

export default function ReportView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [dependency, setDependency] = useState<ReportDependency | null>(null)
  const [dependencyFor, setDependencyFor] = useState<string>('')
  const [loadingDep, setLoadingDep] = useState(false)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('报告模板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析报告列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || '分析报告动作未生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析报告操作失败')
    }
  }

  async function explainDependency(row: Row) {
    const ruleText = String(row.聚合规则 ?? '')
    setLoadingDep(true)
    setDependencyFor(String(row.模板编号))
    setErrorMessage('')
    try {
      setDependency(await previewReportDependency(ruleText))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '依赖解释读取失败')
      setDependency(null)
    } finally {
      setLoadingDep(false)
    }
  }

  return (
    <section className="page" data-module="report">
      <header className="page-head">
        <div>
          <h2>分析报告管理</h2>
          <p className="page-desc">
            报告选取聚合规则时复用与聚合查询预览相同的依赖快照，展示实际采用的指标版本与维度键列，无效依赖无法发布。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('报告模板登记入口尚未接入审批流')}>
            登记报告模板
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分析报告清单
          </button>
        </div>
      </header>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void explainDependency(row)}>
                  依赖解释
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无分析报告数据，可先登记报告模板</td>
            </tr>
          )}
        </tbody>
      </table>

      {loadingDep && <p className="hint-text">正在解析 {dependencyFor} 的聚合规则依赖…</p>}
      {dependency && (
        <div className={`preview-panel ${dependency.ok ? 'preview-ok' : 'preview-bad'}`}>
          <div className="preview-head">
            <span className={`badge ${dependency.ok ? 'badge-ok' : 'badge-bad'}`}>
              {dependency.ok ? '依赖快照一致，可发布' : '存在无效依赖，发布将被阻断'}
            </span>
            <span className="hint-text">
              快照修订号：
              {Object.entries(dependency.snapshotRevisions).map(([module, rev]) => (
                <span key={module} className="snapshot-chip">{module}@{rev}</span>
              ))}
            </span>
          </div>

          {dependency.errors.length > 0 && (
            <ul className="preview-errors">
              {dependency.errors.map((err) => <li key={err}>{err}</li>)}
            </ul>
          )}

          {dependency.ruleRefs.map((ref) => (
            <div key={ref.token} className="rule-dep-card">
              <div className="rule-dep-head">
                <strong>聚合规则 {ref.token}</strong>
                <span className={`badge ${ref.usable ? 'badge-ok' : 'badge-bad'}`}>
                  {ref.usable ? '规则存在' : ref.status}
                </span>
                {ref.reason && <span className="candidate-reason">{ref.reason}</span>}
              </div>
              {ref.preview && (
                <>
                  <div className="snapshot-line">
                    实际采用版本（与聚合查询预览同一快照判断）：
                    {ref.preview.snapshot.metrics.map((m) => (
                      <span key={m.code} className="snapshot-chip">{m.code} {m.name} {m.version}</span>
                    ))}
                    {ref.preview.snapshot.dimensions.map((d) => (
                      <span key={d.code} className="snapshot-chip">{d.code} {d.name} 键列:{d.keyColumn}</span>
                    ))}
                  </div>
                  {!ref.preview.ok && (
                    <ul className="preview-errors">
                      {ref.preview.errors.map((err) => <li key={err}>{err}</li>)}
                    </ul>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条分析报告记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
