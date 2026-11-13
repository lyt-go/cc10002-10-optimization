import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/partition'
const columns = [
  '方案编号',
  '数据表名',
  '分区字段',
  '分区粒度',
  '保留天数',
  '冷热阈值',
  '预建天数',
  '归档路径',
  '当前重评批次',
  '待归档分区数',
  '预计释放存储GB',
]
const actions = ['提交方案审批', '启用方案', '停用方案']
const statuses = ['草稿', '方案审批中', '已启用', '已停用']
const verdicts = ['待归档', '转冷', '保留', '已归档', '已删除']

type Row = Record<string, string | number | null>

type PartitionItem = {
  id: number
  '分区编号': string
  '分区键': string
  '创建时间': string
  '最近访问时间': string
  '存储大小GB': number
  '每日扫描量GB': number
  '归档路径': string
  '分区状态': string
  '重评结论': string
  '结论批次': string
  '结论时间': string
}

type Snapshot = {
  '当前重评批次': string
  '最近重评时间': string
  '重评状态': string
  '待归档分区数': number
  '预计释放存储GB': number
  '预计减少每日扫描GB': number
  '结果来源': string
  '批次': { '批次编号': string; '提交时间': string; '策略编号': string; '策略版本': number } | null
  '批次结果': Record<string, unknown> | null
  '待提交批次': {
    '批次编号': string
    '策略编号': string
    '新增到期分区数': number
    '新增预计释放存储GB': number
    '说明': string
  } | null
}

export default function PartitionView() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const tableFromLink = searchParams.get('table') ?? ''
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>(
    tableFromLink ? { 数据表名: tableFromLink } : {},
  )
  const [refreshToken, setRefreshToken] = useState(0)
  const [selected, setSelected] = useState<Row | null>(null)
  const [partitionFilter, setPartitionFilter] = useState('')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [partitions, setPartitions] = useState<PartitionItem[]>([])
  const [partitionsTotal, setPartitionsTotal] = useState(0)
  const filterFields = useMemo(() => ['方案编号', '数据表名'], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams({ ...filters, size: '50' }).toString()
    try {
      const response = await request(`${ENDPOINT}/snapshots?${query}`)
      if (!response.ok) throw new Error('分区方案列表读取失败')
      const payload = await response.json()
      const items: Row[] = payload.items ?? []
      setRows(items)
      setTotal(payload.total ?? 0)
      // 从成本页带着数据表名跳过来时，直接定位到该表的第一个方案明细。
      if (tableFromLink && items.length > 0 && !selected) {
        void openDetail(items[0])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分区治理列表读取失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, tableFromLink])

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
      if (!payload.ok) throw new Error(payload.message ?? '分区治理动作未生效')
      setNotice(payload.message)
      setRefreshToken((prev) => prev + 1)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分区治理操作失败')
    }
  }

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('分区方案详情读取失败')
      const detail = await response.json()
      setSelected(detail)
      setSnapshot(detail['重评结果'] ?? null)
      setPartitionFilter('')
      const listResponse = await request(
        `${ENDPOINT}/partitions?${new URLSearchParams({ plan_no: String(row['方案编号']), size: '50' }).toString()}`,
      )
      if (!listResponse.ok) throw new Error('分区列表读取失败')
      const listPayload = await listResponse.json()
      setPartitions(listPayload.items ?? [])
      setPartitionsTotal(listPayload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分区方案详情读取失败')
    }
  }

  const archivedCount = partitions.filter((item) => item['重评结论'] === '已归档').length
  const deletedCount = partitions.filter((item) => item['重评结论'] === '已删除').length

  return (
    <section className="page" data-module="partition">
      <header className="page-head">
        <div>
          <h2>分区治理管理</h2>
          <p className="page-desc">
            管理数据表的分区方案，配置分区粒度、保留时长、冷热分层和预建策略。
            分区结论来自重评批次：已归档分区不重复统计，已删除分区只保留历史结果，
            未被新策略影响的分区继续复用上一轮结论。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('分区方案登记入口尚未接入审批流')}>
            登记分区方案
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分区治理清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">分区方案总数</span><strong className="stat-value">{total}</strong></article>
        <article className="stat-card"><span className="stat-label">待归档分区</span><strong className="stat-value">{rows.reduce((sum, row) => sum + Number(row['待归档分区数'] ?? 0), 0)}</strong></article>
        <article className="stat-card"><span className="stat-label">预计释放存储GB</span><strong className="stat-value">{rows.reduce((sum, row) => sum + Number(row['预计释放存储GB'] ?? 0), 0)}</strong></article>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无分区治理数据，可先登记分区方案</td>
            </tr>
          )}
        </tbody>
      </table>

      {selected && snapshot && (
        <section className="panel">
          <header className="panel-head">
            <div>
              <h3>分区方案与分区明细：{String(selected['方案编号'] ?? '')}</h3>
              <p className="page-desc">
                分区详情与存储成本读取同一份重评结果（{snapshot['结果来源']}）；
                成本页只附注归档后预计减少量，不改写历史账单。
              </p>
            </div>
            <div className="row-actions">
              <button className="btn ghost" type="button" onClick={() => navigate('/cost')}>
                查看存储成本
              </button>
              <button className="btn ghost" type="button" onClick={() => setSelected(null)}>关闭</button>
            </div>
          </header>

          <div className="detail-list">
            <span>重评状态：{snapshot['重评状态']}</span>
            <span>当前重评批次：{snapshot['当前重评批次'] || '—'}</span>
            <span>最近重评：{snapshot['最近重评时间'] || '—'}</span>
            <span>待归档分区 {snapshot['待归档分区数']} 个</span>
            <span>预计释放 {snapshot['预计释放存储GB']} GB</span>
            <span>预计减少每日扫描 {snapshot['预计减少每日扫描GB']} GB</span>
          </div>

          {snapshot['待提交批次'] && (
            <p className="batch-metrics">
              <span className="tag warning">待提交批次 {snapshot['待提交批次']['批次编号']}</span>
              <span>
                新增到期 {snapshot['待提交批次']['新增到期分区数']} 个，预计新增释放
                {snapshot['待提交批次']['新增预计释放存储GB']} GB；{snapshot['待提交批次']['说明']}
              </span>
            </p>
          )}

          <div className="form-row">
            <label className="filter-item">
              <span>重评结论</span>
              <select value={partitionFilter} onChange={(event) => setPartitionFilter(event.target.value)}>
                <option value="">全部</option>
                {verdicts.map((verdict) => <option key={verdict} value={verdict}>{verdict}</option>)}
              </select>
            </label>
            <span className="muted-text">
              共 {partitionsTotal} 个分区，已归档 {archivedCount} 个不重复统计，已删除 {deletedCount} 个只保留历史结果
            </span>
          </div>

          <table className="mini-table">
            <thead>
              <tr>
                <th>分区键</th><th>创建时间</th><th>最近访问</th><th>存储 GB</th>
                <th>每日扫描 GB</th><th>归档路径</th><th>分区状态</th><th>重评结论</th>
                <th>结论批次</th><th>结论时间</th>
              </tr>
            </thead>
            <tbody>
              {partitions
                .filter((item) => !partitionFilter || item['重评结论'] === partitionFilter)
                .map((item) => (
                  <tr key={item.id}>
                    <td>{item['分区键']}</td>
                    <td>{item['创建时间']}</td>
                    <td>{item['最近访问时间']}</td>
                    <td>{item['存储大小GB']}</td>
                    <td>{item['每日扫描量GB']}</td>
                    <td>{item['归档路径']}</td>
                    <td>{item['分区状态']}</td>
                    <td>{item['重评结论'] || '—'}</td>
                    <td>{item['结论批次'] || '—'}</td>
                    <td>{item['结论时间'] || '—'}</td>
                  </tr>
                ))}
              {partitions.length === 0 && (
                <tr><td className="empty-state" colSpan={10}>该方案暂无物理分区</td></tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条分区治理记录</span>
        <span>
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
