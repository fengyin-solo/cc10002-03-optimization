import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { request } from '@/api/client'
import {
  Candidate,
  DuplicateGroup,
  PreviewResponse,
  ResolvedRef,
  fetchCandidates,
  fetchDuplicates,
  previewAggregation,
  saveAggregationRule,
} from '@/api/dependencies'

const ENDPOINT = '/api/aggregation'
const columns = [
  '规则编号',
  '聚合函数',
  '目标指标',
  '分组维度',
  '时间范围',
  '筛选条件',
  '采样比例',
  '缓存秒数',
]
const actions = ['提交校验', '启用规则', '停用规则']
const editableFields = ['聚合函数', '目标指标', '分组维度', '时间范围', '筛选条件', '采样比例', '缓存秒数']
const emptyForm: Record<string, string> = {
  规则编号: '',
  聚合函数: 'SUM',
  目标指标: '',
  分组维度: '',
  时间范围: '',
  筛选条件: '',
  采样比例: '1.0',
  缓存秒数: '300',
}

type Row = Record<string, string | number | null>

const statusLabel: Record<string, string> = {
  resolved: '可用',
  ambiguous: '歧义',
  not_found: '引用失效',
  unusable: '状态不可用',
}

export default function AggregationView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [candidates, setCandidates] = useState<{ metrics: Candidate[]; dimensions: Candidate[] } | null>(null)
  const [duplicates, setDuplicates] = useState<DuplicateGroup[]>([])
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<Record<string, string>>(emptyForm)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  // 配置版本号：每次发起预览 +1，只接受与最新版本匹配的响应，杜绝并发串结果。
  const requestSeq = useRef(0)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('聚合规则列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合查询列表读取失败')
    }
  }, [filters])

  const reloadDuplicates = useCallback(async () => {
    try {
      const payload = await fetchDuplicates()
      setDuplicates(payload.groups ?? [])
    } catch {
      /* 重复口径是辅助信息，失败不阻塞主页面 */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    fetchCandidates()
      .then((data) => setCandidates({ metrics: data.metrics, dimensions: data.dimensions }))
      .catch(() => setErrorMessage('跨模块候选加载失败，可稍后重试'))
    void reloadDuplicates()
  }, [reloadDuplicates])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || '聚合查询动作未生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合查询操作失败')
    }
  }

  function openCreate() {
    setEditingId(null)
    setForm({ ...emptyForm })
    setPreview(null)
    setEditorOpen(true)
  }

  function openEditor(row: Row) {
    setEditingId(Number(row.id))
    const next: Record<string, string> = { ...emptyForm }
    for (const field of ['规则编号', ...editableFields]) {
      next[field] = row[field] != null ? String(row[field]) : ''
    }
    setForm(next)
    setPreview(null)
    setEditorOpen(true)
  }

  /** 把候选项追加到对应文字字段；保留历史文字配置的自由编辑能力。 */
  function appendCandidate(field: '目标指标' | '分组维度', code: string) {
    setForm((prev) => {
      const current = prev[field]
        .split(/[,，;；、\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
      if (current.includes(code)) return prev
      current.push(code)
      return { ...prev, [field]: current.join(', ') }
    })
  }

  const runPreview = useCallback(async () => {
    const seq = ++requestSeq.current
    setPreviewing(true)
    setErrorMessage('')
    try {
      const result = await previewAggregation(form)
      // 并发预览只接受与当前配置版本匹配的结果；过期响应直接丢弃。
      if (seq !== requestSeq.current) return
      setPreview(result)
    } catch (error) {
      if (seq !== requestSeq.current) return
      setErrorMessage(error instanceof Error ? error.message : '校验预览失败')
    } finally {
      if (seq === requestSeq.current) setPreviewing(false)
    }
  }, [form])

  async function saveRule() {
    setSaving(true)
    setErrorMessage('')
    try {
      const payload = await saveAggregationRule(editingId, form)
      if (!payload.ok) throw new Error(payload.message)
      setEditorOpen(false)
      await reload()
      await reloadDuplicates()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合规则保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="page" data-module="aggregation">
      <header className="page-head">
        <div>
          <h2>聚合查询管理</h2>
          <p className="page-desc">
            聚合规则跨模块引用指标定义与维度管理，配置前可候选选择、保存前可校验预览，歧义与失效依赖会被明确标出。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={openCreate}>登记聚合规则</button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出聚合查询清单
          </button>
        </div>
      </header>

      {duplicates.length > 0 && (
        <div className="dup-banner">
          <strong>同口径重复预览：</strong>
          {duplicates.map((group) => (
            <span key={group.key} className="dup-chip">
              {group.rules.map((rule) => rule.code).join('、')}（{group.count} 条，指标/维度/聚合函数完全一致）
            </span>
          ))}
        </div>
      )}

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
                <button className="link" type="button" onClick={() => openEditor(row)}>校验预览</button>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无聚合查询数据，可先登记聚合规则</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条聚合查询记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {editorOpen && (
        <div className="drawer-mask" role="presentation" onMouseDown={() => setEditorOpen(false)}>
          <aside className="drawer" role="dialog" aria-label="聚合规则配置与校验预览" onMouseDown={(e) => e.stopPropagation()}>
            <div className="drawer-head">
              <h3>{editingId ? `校验预览：${form.规则编号}` : '登记聚合规则'}</h3>
              <button className="link" type="button" onClick={() => setEditorOpen(false)}>关闭</button>
            </div>

            <div className="drawer-body">
              <div className="form-grid">
                <label className="form-item">
                  <span>规则编号</span>
                  <input value={form.规则编号} disabled={editingId !== null}
                    onChange={(e) => setForm((p) => ({ ...p, 规则编号: e.target.value }))} />
                </label>
                {editableFields.map((field) => (
                  <label key={field} className={`form-item${field === '筛选条件' ? ' wide' : ''}`}>
                    <span>{field}</span>
                    <input
                      value={form[field]}
                      placeholder={field === '目标指标' || field === '分组维度' ? '可手填编号/名称，多个引用用逗号分隔' : `输入${field}`}
                      onChange={(e) => setForm((p) => ({ ...p, [field]: e.target.value }))}
                    />
                  </label>
                ))}
              </div>

              {candidates && (
                <div className="candidate-block">
                  <CandidatePicker
                    title="指标定义候选（点击编号引用；同名不同编号需自行选定，系统不会猜）"
                    candidates={candidates.metrics}
                    onPick={(code) => appendCandidate('目标指标', code)}
                    versionField="version"
                  />
                  <CandidatePicker
                    title="维度管理候选（展示维度键列与不可用原因）"
                    candidates={candidates.dimensions}
                    onPick={(code) => appendCandidate('分组维度', code)}
                    versionField="keyColumn"
                  />
                </div>
              )}

              <div className="drawer-actions">
                <button className="btn primary" type="button" disabled={previewing} onClick={() => void runPreview()}>
                  {previewing ? '解析中…' : '运行校验预览'}
                </button>
                <button className="btn" type="button" disabled={saving} onClick={() => void saveRule()}>
                  {saving ? '保存中…' : '保存配置'}
                </button>
                <span className="hint-text">文字配置保持可自由编辑；保存与启用均以服务端校验为准</span>
              </div>

              {preview && <PreviewPanel preview={preview} />}
            </div>
          </aside>
        </div>
      )}
    </section>
  )
}

function CandidatePicker({
  title,
  candidates,
  onPick,
  versionField,
}: {
  title: string
  candidates: Candidate[]
  onPick: (code: string) => void
  versionField: 'version' | 'keyColumn'
}) {
  const [keyword, setKeyword] = useState('')
  const filtered = candidates.filter((item) =>
    !keyword || item.code.includes(keyword) || item.name.includes(keyword))
  return (
    <div className="candidate-picker">
      <div className="candidate-title">
        <span>{title}</span>
        <input value={keyword} placeholder="按编号/名称过滤" onChange={(e) => setKeyword(e.target.value)} />
      </div>
      <ul className="candidate-list">
        {filtered.map((item) => (
          <li key={item.id} className={item.usable ? '' : 'is-disabled'}>
            <button type="button" disabled={!item.usable} onClick={() => onPick(item.code)}>
              {item.code}
            </button>
            <span className="candidate-name">{item.name}</span>
            <span className="candidate-ver">{versionField === 'version' ? `版本 ${item.version ?? '—'}` : `键列 ${item.keyColumn ?? '—'}`}</span>
            <span className={`badge ${item.usable ? 'badge-ok' : 'badge-bad'}`}>{item.status}</span>
            {!item.usable && <span className="candidate-reason" title={item.reason}>{item.reason}</span>}
          </li>
        ))}
        {filtered.length === 0 && <li className="empty-state">没有匹配的候选</li>}
      </ul>
    </div>
  )
}

function PreviewPanel({ preview }: { preview: PreviewResponse }) {
  return (
    <div className={`preview-panel ${preview.ok ? 'preview-ok' : 'preview-bad'}`}>
      <div className="preview-head">
        <span className={`badge ${preview.ok ? 'badge-ok' : 'badge-bad'}`}>
          {preview.ok ? '依赖全部有效' : '依赖校验未通过'}
        </span>
        <span className="hint-text">
          配置指纹 {preview.fingerprint} ｜ 缓存 {preview.cache.preview === 'hit' ? '命中（同快照复用）' : '未命中（重新解析）'}
        </span>
      </div>

      <div className="snapshot-line">
        依赖快照修订号：
        {Object.entries(preview.snapshot.revisions).map(([module, rev]) => (
          <span key={module} className="snapshot-chip">{module}@{rev}</span>
        ))}
      </div>

      {preview.errors.length > 0 && (
        <ul className="preview-errors">
          {preview.errors.map((err) => <li key={err}>{err}</li>)}
        </ul>
      )}

      <RefTable title="目标指标解析（展示实际采用的指标版本）" refs={preview.metricRefs} kindLabel="指标" />
      <RefTable title="分组维度解析（展示实际采用的维度键列）" refs={preview.dimensionRefs} kindLabel="维度" />
    </div>
  )
}

function RefTable({ title, refs, kindLabel }: { title: string; refs: ResolvedRef[]; kindLabel: string }) {
  if (refs.length === 0) return null
  return (
    <div className="ref-table">
      <h4>{title}</h4>
      <table className="data-table compact">
        <thead>
          <tr>
            <th>引用文字</th><th>结论</th><th>{kindLabel}编号</th><th>名称</th>
            <th>{kindLabel === '指标' ? '口径版本' : '维度键列'}</th><th>不能使用的原因 / 歧义候选</th>
          </tr>
        </thead>
        <tbody>
          {refs.map((ref) => (
            <tr key={ref.token} className={ref.usable ? '' : 'row-invalid'}>
              <td>{ref.token}</td>
              <td><span className={`badge ${ref.usable ? 'badge-ok' : 'badge-bad'}`}>{statusLabel[ref.status] ?? ref.status}</span></td>
              <td>{ref.selected?.code ?? '—'}</td>
              <td>{ref.selected?.name ?? '—'}</td>
              <td>{kindLabel === '指标' ? (ref.selected?.version ?? '—') : (ref.selected?.keyColumn ?? '—')}</td>
              <td>
                {ref.reason || '—'}
                {ref.status === 'ambiguous' && (
                  <ul className="ambiguous-list">
                    {ref.candidates.map((c) => (
                      <li key={c.id}>{c.code}（{c.name}，版本 {c.version ?? c.keyColumn ?? '—'}，{c.status}）</li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
