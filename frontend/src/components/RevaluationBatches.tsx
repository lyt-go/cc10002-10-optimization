import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

type AffectedPartition = {
  '分区编号': string
  '分区键': string
  '创建时间': string
  '最近访问时间': string
  '存储大小GB': number
  '每日扫描量GB': number
  '归档路径': string
  '原结论': string
}

type PlanResult = {
  '方案编号': string
  '数据表名': string
  '旧保留天数': number | null
  '新保留天数': number | null
  '旧冷数据阈值': number | null
  '新冷数据阈值': number | null
  '参数变化': boolean
  '新增到期分区数': number
  '新增转冷分区数': number
  '移除到期分区数': number
  '解除转冷分区数': number
  '沿用结论分区数': number
  '已归档跳过数': number
  '已删除保留数': number
  '待归档分区数': number
  '预计释放存储GB': number
  '预计减少每日扫描GB': number
  '新增预计释放存储GB': number
  '新增预计减少每日扫描GB': number
  '新增到期分区': AffectedPartition[]
  '新增转冷分区': AffectedPartition[]
  '说明': string
}

type Batch = {
  id: number
  '批次编号': string
  '策略编号': string
  '数据表名': string
  '策略版本': number
  '旧保留天数': number | null
  '新保留天数': number | null
  '旧冷数据阈值': number | null
  '新冷数据阈值': number | null
  '批次状态': string
  '生成时间': string
  '提交时间': string
  '提交人': string
  '受影响方案数': number
  '新增到期分区数': number
  '沿用结论分区数': number
  '待归档分区数': number
  '预计释放存储GB': number
  '预计减少每日扫描GB': number
  '失效原因'?: string
  '结果': PlanResult[]
}

const STATUS_STYLE: Record<string, string> = {
  待提交: 'tag warning',
  已提交: 'tag success',
  已失效: 'tag muted',
}

export default function RevaluationBatches({
  policyNo,
  refreshToken = 0,
  onSubmitted,
}: {
  policyNo?: string
  refreshToken?: number
  onSubmitted?: () => void
}) {
  const [batches, setBatches] = useState<Batch[]>([])
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState<number | null>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams({ size: '50' })
    if (policyNo) query.set('policy_no', policyNo)
    try {
      const response = await request(`/api/revaluation?${query.toString()}`)
      if (!response.ok) throw new Error('重评批次读取失败')
      const payload = await response.json()
      setBatches(payload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '重评批次读取失败')
    }
  }, [policyNo])

  useEffect(() => {
    void reload()
  }, [reload, refreshToken])

  async function submit(batch: Batch) {
    setErrorMessage('')
    setNotice('')
    setBusy(batch.id)
    try {
      const response = await request(`/api/revaluation/${batch.id}/submit`, {
        method: 'POST',
        body: JSON.stringify({ values: { 操作人: '值班管理员' } }),
      })
      const payload = await response.json().catch(() => ({ ok: false, message: '提交结果无法解析' }))
      if (!payload.ok) throw new Error(payload.message ?? '重评批次提交失败')
      setNotice(payload.message)
      await reload()
      onSubmitted?.()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '重评批次提交失败')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h3>生命周期重评批次</h3>
          <p className="page-desc">
            策略启用时按批量分区生成一版待提交结果；一次提交后才更新分区方案，并同步写变更审计。
            已归档分区不重复统计，已删除分区只保留历史结论，未受影响的分区沿用上一轮结果。
          </p>
        </div>
        <span className="hint">提交前只读，不修改当前计划</span>
      </header>

      <div className="batch-list">
        {batches.map((batch) => {
          const isExpanded = expanded[batch.id] ?? batch['批次状态'] === '待提交'
          return (
            <article key={batch.id} className="batch-card">
              <div className="batch-head">
                <div className="batch-title">
                  <strong>{batch['批次编号']}</strong>
                  <span className={STATUS_STYLE[batch['批次状态']] ?? 'tag'}>{batch['批次状态']}</span>
                  <span className="muted-text">
                    {batch['策略编号']} v{batch['策略版本']} · {batch['数据表名']} · 生成 {batch['生成时间']}
                  </span>                </div>
                <div className="row-actions">
                  <button
                    className="link"
                    type="button"
                    onClick={() => setExpanded((prev) => ({ ...prev, [batch.id]: !isExpanded }))}
                  >
                    {isExpanded ? '收起逐条结果' : '展开逐条结果'}
                  </button>
                  {batch['批次状态'] === '待提交' && (
                    <button
                      className="btn primary"
                      type="button"
                      disabled={busy === batch.id}
                      onClick={() => void submit(batch)}
                    >
                      {busy === batch.id ? '提交中…' : '一次提交并更新计划'}
                    </button>
                  )}
                </div>
              </div>

              <div className="batch-metrics">
                <span>保留天数 {batch['旧保留天数'] ?? '未生效'} → {batch['新保留天数']}</span>
                <span>冷热阈值 {batch['旧冷数据阈值'] ?? '未生效'} → {batch['新冷数据阈值']}</span>
                <span>受影响方案 {batch['受影响方案数']} 条</span>
                <span>新增到期 {batch['新增到期分区数']} 个</span>
                <span>沿用上一轮结论 {batch['沿用结论分区数']} 个</span>
                <span>预计释放 {batch['预计释放存储GB']} GB / 减少扫描 {batch['预计减少每日扫描GB']} GB</span>
                {batch['提交时间'] && <span>提交 {batch['提交时间']}（{batch['提交人']}）</span>}
                {batch['失效原因'] && <span className="error-text">{batch['失效原因']}</span>}
              </div>

              {isExpanded && (
                <div className="batch-results">
                  {batch['结果'].map((result) => (
                    <div key={result['方案编号']} className="result-card">
                      <div className="result-head">
                        <strong>{result['方案编号']}</strong>
                        <span className="muted-text">{result['数据表名']} · {result['说明']}</span>
                      </div>
                      <div className="result-grid">
                        <span>参数变化：{result['参数变化'] ? '是' : '否（沿用）'}</span>
                        <span>新增到期 {result['新增到期分区数']} / 新增转冷 {result['新增转冷分区数']}</span>
                        <span>移除到期 {result['移除到期分区数']} / 解除转冷 {result['解除转冷分区数']}</span>
                        <span>沿用结论 {result['沿用结论分区数']}</span>
                        <span>已归档跳过 {result['已归档跳过数']} / 已删除保留 {result['已删除保留数']}</span>
                        <span>待归档 {result['待归档分区数']}，预计释放 {result['预计释放存储GB']} GB</span>
                      </div>
                      {result['新增到期分区'].length > 0 && (
                        <table className="mini-table">
                          <thead>
                            <tr>
                              <th>新增到期分区</th><th>创建时间</th><th>最近访问</th>
                              <th>存储 GB</th><th>每日扫描 GB</th><th>归档路径</th><th>原结论</th>
                            </tr>
                          </thead>
                          <tbody>
                            {result['新增到期分区'].map((partition) => (
                              <tr key={partition['分区编号']}>
                                <td>{partition['分区键']}</td>
                                <td>{partition['创建时间']}</td>
                                <td>{partition['最近访问时间']}</td>
                                <td>{partition['存储大小GB']}</td>
                                <td>{partition['每日扫描量GB']}</td>
                                <td>{partition['归档路径']}</td>
                                <td>{partition['原结论']}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  ))}
                  {batch['结果'].length === 0 && (
                    <p className="empty-state">该策略对应表没有已启用的分区方案，批次不产生任何计划变更。</p>
                  )}
                </div>
              )}
            </article>
          )
        })}
        {batches.length === 0 && (
          <p className="empty-state">暂无重评批次。调整并启用生命周期策略后会生成待提交批次。</p>
        )}
      </div>

      {(errorMessage || notice) && (
        <footer className="page-foot">
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </footer>
      )}
    </section>
  )
}
