import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import RevaluationBatches from '@/components/RevaluationBatches'

const ENDPOINT = '/api/lifecycle'
const columns = [
  '策略编号',
  '数据表名',
  '保留天数',
  '冷数据阈值',
  '归档格式',
  '删除确认',
  '归档路径',
  '最后执行时间',
  '策略版本',
  '生效版本',
]
const actions = ['提交策略评审', '启用策略', '停用策略']
const statuses = ['草稿', '策略评审中', '已启用', '已停用']
const adjustFields = ['保留天数', '冷数据阈值', '归档路径']

type Row = Record<string, string | number | null>

export default function LifecycleView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [refreshToken, setRefreshToken] = useState(0)
  const [selected, setSelected] = useState<Row | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('生命周期策略列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据生命周期列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload, refreshToken])

  async function runAction(action: string, row: Row, values: Record<string, unknown> = {}) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action, ...values } }),
      })
      const payload = await response.json().catch(() => ({ ok: false, message: '动作结果无法解析' }))
      if (!payload.ok) throw new Error(payload.message ?? '数据生命周期动作未生效')
      setNotice(payload.message)
      setRefreshToken((prev) => prev + 1)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据生命周期操作失败')
    }
  }

  async function adjust(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const payloadValues: Record<string, unknown> = {
        // 带上读取时的策略版本：并发调整时只有一个版本能通过乐观锁。
        策略版本: row['策略版本'],
        ...draft,
      }
      const response = await request(`${ENDPOINT}/${row.id}/adjust`, {
        method: 'POST',
        body: JSON.stringify({ values: payloadValues }),
      })
      const payload = await response.json().catch(() => ({ ok: false, message: '调整结果无法解析' }))
      if (!payload.ok) throw new Error(payload.message ?? '策略参数调整失败')
      setNotice(payload.message)
      setDraft({})
      setRefreshToken((prev) => prev + 1)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '策略参数调整失败')
    }
  }

  const enabledCount = rows.filter((row) => row.status === '已启用').length
  const reviewCount = rows.filter((row) => row.status === '策略评审中').length
  const modifiedCount = rows.filter(
    (row) => Number(row['生效版本'] ?? 0) < Number(row['策略版本'] ?? 0),
  ).length

  return (
    <section className="page" data-module="lifecycle">
      <header className="page-head">
        <div>
          <h2>数据生命周期管理</h2>
          <p className="page-desc">
            管理数据的生命周期策略，定义冷热分层、归档和过期删除的规则。
            保留天数或冷热阈值变化不会直接改写存量分区：启用策略只生成待提交重评批次，
            提交后才按新增到期区间更新分区计划。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据生命周期清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">生命周期策略总数</span><strong className="stat-value">{total}</strong></article>
        <article className="stat-card"><span className="stat-label">已启用策略</span><strong className="stat-value">{enabledCount}</strong></article>
        <article className="stat-card"><span className="stat-label">策略评审中</span><strong className="stat-value">{reviewCount}</strong></article>
        <article className="stat-card"><span className="stat-label">参数已调整待重评</span><strong className="stat-value">{modifiedCount}</strong></article>
      </div>

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
          {rows.map((row) => {
            const pendingReview = Number(row['生效版本'] ?? 0) < Number(row['策略版本'] ?? 0)
            return (
              <tr
                key={String(row.id)}
                className="clickable-row"
                onClick={() => {
                  setSelected(row)
                  setDraft({})
                }}
              >
                {columns.map((column) => {
                  const value = row[column]
                  if (column === '策略版本' && pendingReview) {
                    return <td key={column}>{String(value ?? '')}（待重评）</td>
                  }
                  return <td key={column}>{value ?? '—'}</td>
                })}
                <td className="row-actions" onClick={(event) => event.stopPropagation()}>
                  {actions.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无数据生命周期数据，可先登记生命周期策略</td>
            </tr>
          )}
        </tbody>
      </table>

      {selected && (
        <section className="panel">
          <header className="panel-head">
            <div>
              <h3>调整策略参数：{String(selected['策略编号'] ?? '')}</h3>
              <p className="page-desc">
                调整后版本 +1 并退回草稿等待重新评审；生效版本保持原值，直到新版本评审并启用。
                并发调整时以读取到的策略版本做乐观锁，只有一个版本能生效。
              </p>
            </div>
            <button className="btn ghost" type="button" onClick={() => setSelected(null)}>关闭</button>
          </header>
          <div className="form-row">
            {adjustFields.map((field) => (
              <label key={field} className="filter-item">
                <span>{field}</span>
                <input
                  value={draft[field] ?? String(selected[field] ?? '')}
                  onChange={(event) => setDraft((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              </label>
            ))}
            <button className="btn primary" type="button" onClick={() => void adjust(selected)}>
              提交参数调整
            </button>
          </div>
          <div className="detail-list">
            <span>当前策略版本 v{String(selected['策略版本'] ?? 1)}</span>
            <span>生效版本 v{String(selected['生效版本'] ?? 0)}</span>
            <span>生效保留天数 {String(selected['生效保留天数'] ?? '未生效')}</span>
            <span>生效冷阈值 {String(selected['生效冷数据阈值'] ?? '未生效')}</span>
          </div>
        </section>
      )}

      <RevaluationBatches refreshToken={refreshToken} onSubmitted={() => setRefreshToken((prev) => prev + 1)} />

      <footer className="page-foot">
        <span>共 {total} 条数据生命周期记录</span>
        <span>
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
