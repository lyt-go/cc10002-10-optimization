import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/cost'
const columns = [
  '账单编号',
  '数据表名',
  '存储大小GB',
  '每日扫描量GB',
  '查询次数',
  '存储成本',
  '计算成本',
  '统计月份',
  '归档预计减少存储GB',
  '归档预计减少扫描GB',
]
const actions = ['生成账单', '核对账单', '归档账单']
const statuses = ['草稿', '已生成', '已核对', '已归档']

type Row = Record<string, string | number | null>

type ArchiveImpact = {
  '结果来源': string
  '批次编号': string
  '提交时间'?: string
  '待归档分区数': number
  '预计减少存储GB': number
  '预计减少每日扫描GB': number
  '方案': {
    '方案编号': string
    '新增到期分区数': number
    '待归档分区数': number
    '预计释放存储GB': number
    '预计减少每日扫描GB': number
    '说明': string
    '新增到期分区': { '分区键': string; '存储大小GB': number; '每日扫描量GB': number }[]
  }[]
}

export default function CostView() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [refreshToken, setRefreshToken] = useState(0)
  const [selected, setSelected] = useState<Row | null>(null)
  const [impact, setImpact] = useState<ArchiveImpact | null>(null)
  const filterFields = useMemo(() => ['账单编号', '数据表名'], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams({ ...filters, size: '50' }).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('成本账单列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '存储成本列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload, refreshToken])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json().catch(() => ({ ok: false, message: '动作结果无法解析' }))
      if (!payload.ok) throw new Error(payload.message ?? '存储成本动作未生效')
      setNotice(payload.message)
      setRefreshToken((prev) => prev + 1)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '存储成本操作失败')
    }
  }

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('成本账单详情读取失败')
      const detail = await response.json()
      setSelected(detail)
      setImpact(detail['归档预计'] ?? null)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '成本账单详情读取失败')
    }
  }

  return (
    <section className="page" data-module="cost">
      <header className="page-head">
        <div>
          <h2>存储成本管理</h2>
          <p className="page-desc">
            追踪各数据表的存储和计算成本，为优化决策提供成本数据支持。
            归档预计来自与分区详情同一份重评批次，只说明归档后预计减少的存储量与计算扫描量，
            历史账单字段保持原值。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('成本账单登记入口尚未接入审批流')}>
            登记成本账单
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出存储成本清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">成本账单总数</span><strong className="stat-value">{total}</strong></article>
        <article className="stat-card"><span className="stat-label">已核对账单</span><strong className="stat-value">{rows.filter((row) => row.status === '已核对').length}</strong></article>
        <article className="stat-card"><span className="stat-label">归档预计减少存储GB</span><strong className="stat-value">{rows.reduce((sum, row) => sum + Number(row['归档预计减少存储GB'] ?? 0), 0)}</strong></article>
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
          {rows.map((row) => (
            <tr key={String(row.id)} className="clickable-row" onClick={() => void openDetail(row)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions" onClick={(event) => event.stopPropagation()}>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无存储成本数据，可先登记成本账单</td>
            </tr>
          )}
        </tbody>
      </table>

      {selected && impact && (
        <section className="panel">
          <header className="panel-head">
            <div>
              <h3>成本详情：{String(selected['账单编号'] ?? '')}</h3>
              <p className="page-desc">
                账单口径：存储 {String(selected['存储大小GB'])} GB，每日扫描 {String(selected['每日扫描量GB'])} GB，
                存储成本 {String(selected['存储成本'])}，计算成本 {String(selected['计算成本'])}（
                {String(selected['统计月份'])}）。以下预计不参与账单计算，也不改写历史账单。
              </p>
            </div>
            <div className="row-actions">
              <button
                className="btn ghost"
                type="button"
                onClick={() => navigate(`/partition?table=${encodeURIComponent(String(selected['数据表名'] ?? ''))}`)}
              >
                查看同一重评结果的分区明细
              </button>
              <button className="btn ghost" type="button" onClick={() => setSelected(null)}>关闭</button>
            </div>
          </header>

          <div className="detail-list">
            <span>数据来源：{impact['结果来源']}</span>
            <span>批次：{impact['批次编号'] || '—'}</span>
            <span>待归档分区 {impact['待归档分区数']} 个</span>
            <span>归档后预计减少存储 {impact['预计减少存储GB']} GB</span>
            <span>归档后预计减少每日扫描 {impact['预计减少每日扫描GB']} GB</span>
          </div>

          {impact['方案'].map((plan) => (
            <div key={plan['方案编号']} className="result-card">
              <div className="result-head">
                <strong>{plan['方案编号']}</strong>
                <span className="muted-text">{plan['说明']}</span>
              </div>
              <div className="result-grid">
                <span>新增到期 {plan['新增到期分区数']} 个</span>
                <span>待归档 {plan['待归档分区数']} 个（累计）</span>
                <span>预计释放 {plan['预计释放存储GB']} GB</span>
                <span>预计减少扫描 {plan['预计减少每日扫描GB']} GB/日</span>
              </div>
              {plan['新增到期分区'].length > 0 && (
                <table className="mini-table">
                  <thead>
                    <tr><th>新增到期分区</th><th>存储 GB</th><th>每日扫描 GB</th></tr>
                  </thead>
                  <tbody>
                    {plan['新增到期分区'].map((partition) => (
                      <tr key={partition['分区键']}>
                        <td>{partition['分区键']}</td>
                        <td>{partition['存储大小GB']}</td>
                        <td>{partition['每日扫描量GB']}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
          {impact['方案'].length === 0 && (
            <p className="empty-state">尚无已提交重评批次，暂不给出归档预计。</p>
          )}
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条存储成本记录</span>
        <span>
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
