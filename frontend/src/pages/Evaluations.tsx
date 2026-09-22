import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, ClipboardCheck, Clock3, RefreshCw, Target } from 'lucide-react'
import {
  evaluationsApi,
  type AgentPredictionFilters,
  type AgentPredictionGroup,
  type AgentPredictionListResponse,
  type AgentPredictionOutcomeItem,
  type AgentPredictionSummary,
  type EvaluationHorizonUnit,
} from '@panwatch/api'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

type FilterState = {
  agentName: string
  market: string
  action: string
  status: string
  horizonUnit: EvaluationHorizonUnit
  days: number
  startDate: string
  endDate: string
}

const INITIAL_FILTERS: FilterState = {
  agentName: 'all', market: 'all', action: 'all', status: 'all',
  horizonUnit: 'trading_days', days: 90, startDate: '', endDate: '',
}

const ACTION_LABELS: Record<string, string> = {
  buy: 'Mua vào', add: 'Gia tăng tỷ trọng', sell: 'Bán ra', reduce: 'Hạ tỷ trọng', avoid: 'Tránh ra', hold: 'Nắm giữ', watch: 'Quan sát',
}

function toApiFilters(filters: FilterState): AgentPredictionFilters {
  return {
    agentName: filters.agentName === 'all' ? undefined : filters.agentName,
    market: filters.market === 'all' ? undefined : filters.market,
    action: filters.action === 'all' ? undefined : filters.action,
    status: filters.status === 'all' ? undefined : filters.status,
    horizonUnit: filters.horizonUnit, days: filters.days,
    startDate: filters.startDate || undefined, endDate: filters.endDate || undefined, limit: 200,
  }
}

function formatPct(value: number | null | undefined) {
  if (value == null) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function pctClass(value: number | null | undefined) {
  if (value == null || value === 0) return 'text-muted-foreground'
  return value > 0 ? 'text-rose-500' : 'text-emerald-500'
}

function outcomeLabel(outcome?: AgentPredictionOutcomeItem) {
  if (!outcome) return 'Chưa ghi nhận'
  if (outcome.status === 'pending') return 'Chờ điền lại'
  if (outcome.status === 'no_base_price') return 'Không có giá gốc'
  return outcome.hit === true ? 'Trúng' : outcome.hit === false ? 'Không trúng' : 'Không xác định được'
}

function OutcomeCell({ outcome }: { outcome?: AgentPredictionOutcomeItem }) {
  if (!outcome || outcome.status === 'pending') return <span className="text-[12px] text-muted-foreground">{outcomeLabel(outcome)}</span>
  return <div className="text-right"><div className={`font-mono text-[12px] ${pctClass(outcome.return_pct)}`}>{formatPct(outcome.return_pct)}</div><div className={`text-[10px] ${outcome.hit ? 'text-emerald-600' : 'text-muted-foreground'}`}>{outcomeLabel(outcome)}</div></div>
}

function SummaryCard({ label, value, hint, tone = 'default' }: { label: string; value: string; hint?: string; tone?: 'default' | 'positive' | 'warning' }) {
  const valueClass = tone === 'positive' ? 'text-emerald-600' : tone === 'warning' ? 'text-amber-600' : 'text-foreground'
  return <div className="rounded-xl border border-border/60 bg-card/70 p-3.5"><div className="text-[11px] text-muted-foreground">{label}</div><div className={`mt-1 text-xl font-bold ${valueClass}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-muted-foreground">{hint}</div>}</div>
}

export default function EvaluationsPage() {
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS)
  const [data, setData] = useState<AgentPredictionListResponse | null>(null)
  const [summary, setSummary] = useState<AgentPredictionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [evaluating, setEvaluating] = useState(false)
  const [selected, setSelected] = useState<AgentPredictionGroup | null>(null)
  const targetGroupId = searchParams.get('prediction_group_id') || ''
  const apiFilters = useMemo(() => toApiFilters(filters), [filters])
  const rows = data?.items || []
  const options = data?.available_filters
  const policy = data?.policy || summary?.policy
  const oneDay = summary?.horizons['1']
  const fiveDay = summary?.horizons['5']

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, nextSummary] = await Promise.all([
        evaluationsApi.listAgentPredictions(apiFilters), evaluationsApi.getAgentPredictionSummary(apiFilters),
      ])
      setData(list)
      setSummary(nextSummary)
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Tải dữ liệu kiểm chứng thất bại', 'error')
    } finally {
      setLoading(false)
    }
  }, [apiFilters, toast])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!targetGroupId || !data) return
    const target = data.items.find(item => item.prediction_group_id === targetGroupId)
    if (target) setSelected(target)
  }, [data, targetGroupId])

  const updateFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => setFilters(current => ({ ...current, [key]: value }))
  const handleEvaluate = async () => {
    setEvaluating(true)
    try {
      const result = await evaluationsApi.evaluateAgentPredictions()
      toast(`Kiểm tra xong: điền lại thêm ${result.evaluated} bản, còn ${result.skipped_not_due} bản chưa tới hạn`, 'success')
      await load()
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Kiểm tra khuyến nghị thất bại', 'error')
    } finally {
      setEvaluating(false)
    }
  }

  return <div className="w-full space-y-4 md:space-y-6">
    <section className="card overflow-hidden">
      <div className="p-4 md:p-5 border-b border-border/60">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3"><div className="w-10 h-10 shrink-0 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center shadow-sm"><ClipboardCheck className="w-5 h-5 text-white" /></div><div><div className="flex items-center gap-2 flex-wrap"><h1 className="text-lg md:text-xl font-bold">Kiểm chứng</h1><Badge variant="secondary">Ôn lại khuyến nghị của Agent</Badge></div><p className="mt-1 text-[12px] md:text-[13px] text-muted-foreground">Đối chiếu khuyến nghị đã đưa với diễn biến thực tế sau đó; quyết định lịch sử 1/5/20 của TA cho từng mã vẫn nằm trong phần chi tiết phân tích.</p></div></div>
          <Button variant="outline" size="sm" onClick={() => void handleEvaluate()} disabled={evaluating}><RefreshCw className={`w-3.5 h-3.5 ${evaluating ? 'animate-spin' : ''}`} />{evaluating ? 'Đang kiểm tra' : 'Kiểm tra khuyến nghị đã tới hạn'}</Button>
        </div>
      </div>
      <div className="p-4 md:p-5 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3"><SummaryCard label="Khuyến nghị đã ghi nhận" value={String(summary?.suggestion_count ?? '--')} hint="Gộp trùng theo từng lần khuyến nghị" /><SummaryCard label="Chờ điền lại" value={String(summary?.pending_count ?? '--')} hint="Chưa đủ số phiên để hậu kiểm" tone="warning" /><SummaryCard label="Trúng sau 1 phiên giao dịch" value={oneDay?.hit_rate != null ? `${(oneDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`Mẫu ${oneDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="Trúng sau 5 phiên giao dịch" value={fiveDay?.hit_rate != null ? `${(fiveDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`Mẫu ${fiveDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="Lợi nhuận bình quân 5 phiên" value={formatPct(fiveDay?.avg_return_pct)} hint="Chỉ tính các phiên đã hoàn tất" /></div>
        {summary?.insufficient_sample && <div className="flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300"><Target className="w-3.5 h-3.5 shrink-0" />Mẫu đã hoàn tất 5 phiên giao dịch chưa đủ 20 bản, tỷ lệ trúng chỉ để ôn lại tham khảo, tạm chưa phải kết luận ổn định.</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 pt-1">
          <Select value={filters.agentName} onValueChange={value => updateFilter('agentName', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="Mọi Agent" /></SelectTrigger><SelectContent><SelectItem value="all">Mọi Agent</SelectItem>{options?.agent_names.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.market} onValueChange={value => updateFilter('market', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="Mọi thị trường" /></SelectTrigger><SelectContent><SelectItem value="all">Mọi thị trường</SelectItem>{options?.markets.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.action} onValueChange={value => updateFilter('action', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="Mọi hành động" /></SelectTrigger><SelectContent><SelectItem value="all">Mọi hành động</SelectItem>{options?.actions.map(value => <SelectItem key={value} value={value}>{ACTION_LABELS[value] || value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.status} onValueChange={value => updateFilter('status', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="Mọi trạng thái" /></SelectTrigger><SelectContent><SelectItem value="all">Mọi trạng thái</SelectItem>{options?.statuses.map(value => <SelectItem key={value} value={value}>{value === 'evaluated' ? 'Đã kiểm chứng' : value === 'pending' ? 'Chờ điền lại' : value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.horizonUnit} onValueChange={value => updateFilter('horizonUnit', value as EvaluationHorizonUnit)}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="trading_days">Khẩu độ phiên giao dịch</SelectItem><SelectItem value="calendar_days_legacy">Khẩu độ ngày tự nhiên (cách cũ)</SelectItem><SelectItem value="all">Mọi khẩu độ</SelectItem></SelectContent></Select>
          <Select value={String(filters.days)} onValueChange={value => updateFilter('days', Number(value))}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="30">30 ngày gần nhất</SelectItem><SelectItem value="90">90 ngày gần nhất</SelectItem><SelectItem value="180">180 ngày gần nhất</SelectItem><SelectItem value="365">365 ngày gần nhất</SelectItem></SelectContent></Select>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-start">Ngày bắt đầu</Label><Input id="evaluation-start" type="date" className="h-8 text-[11px]" value={filters.startDate} onChange={event => updateFilter('startDate', event.target.value)} /></div>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-end">Ngày kết thúc</Label><Input id="evaluation-end" type="date" className="h-8 text-[11px]" value={filters.endDate} onChange={event => updateFilter('endDate', event.target.value)} /></div>
        </div>
      </div>
    </section>
    <section className="card overflow-hidden"><div className="px-4 md:px-5 py-3 border-b border-border/60 flex items-center justify-between"><div className="text-[13px] font-semibold">Chi tiết khuyến nghị</div><div className="text-[11px] text-muted-foreground">Tổng {data?.total ?? 0} khuyến nghị</div></div>{loading ? <div className="py-14 text-center text-[13px] text-muted-foreground">Đang tải phần ôn lại khuyến nghị…</div> : rows.length === 0 ? <div className="py-14 text-center text-[13px] text-muted-foreground">Bộ lọc hiện tại chưa có bản ghi khuyến nghị nào</div> : <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-[12px]"><thead className="bg-accent/20 text-muted-foreground text-[11px]"><tr className="border-b border-border/50"><th className="py-2.5 px-4 text-left font-medium">Ngày khuyến nghị</th><th className="py-2.5 px-2 text-left font-medium">Mã</th><th className="py-2.5 px-2 text-left font-medium">Nguồn</th><th className="py-2.5 px-2 text-left font-medium">Hành động</th><th className="py-2.5 px-2 text-right font-medium">Độ tin cậy</th><th className="py-2.5 px-2 text-right font-medium">Giá khuyến nghị</th><th className="py-2.5 px-3 text-right font-medium">1 phiên giao dịch</th><th className="py-2.5 px-4 text-right font-medium">5 phiên giao dịch</th></tr></thead><tbody>{rows.map(row => <tr key={row.prediction_group_id} onClick={() => setSelected(row)} className="border-b border-border/40 cursor-pointer hover:bg-accent/30 transition-colors"><td className="py-3 px-4 font-mono text-muted-foreground">{row.prediction_date}</td><td className="py-3 px-2 font-medium">{row.stock_symbol}<span className="ml-1 text-[10px] text-muted-foreground">{row.stock_market}</span></td><td className="py-3 px-2 text-muted-foreground">{row.agent_name}</td><td className="py-3 px-2"><Badge variant="secondary" className="px-1.5 py-0.5">{row.action_label || ACTION_LABELS[row.action] || row.action}</Badge>{row.is_legacy_group && <span className="ml-1.5 text-[10px] text-amber-600">Khẩu độ cũ</span>}</td><td className="py-3 px-2 text-right font-mono">{row.confidence == null ? '--' : row.confidence.toFixed(2)}</td><td className="py-3 px-2 text-right font-mono">{row.trigger_price == null ? '--' : row.trigger_price.toFixed(2)}</td><td className="py-3 px-3"><OutcomeCell outcome={row.outcomes['1']} /></td><td className="py-3 px-4"><OutcomeCell outcome={row.outcomes['5']} /></td></tr>)}</tbody></table></div>}</section>
    <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}><DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto"><DialogHeader><DialogTitle>{selected ? `${selected.stock_symbol} · ${selected.action_label || selected.action}` : 'Chi tiết hậu kiểm khuyến nghị'}</DialogTitle><DialogDescription>{selected?.prediction_date} · {selected?.agent_name} · {selected?.stock_market}</DialogDescription></DialogHeader>{selected && <div className="space-y-4 text-[13px]"><div className="grid grid-cols-3 gap-3 rounded-xl bg-accent/30 p-3"><div><div className="text-[10px] text-muted-foreground">Độ tin cậy</div><div className="mt-1 font-medium">{selected.confidence == null ? '--' : selected.confidence.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">Giá khuyến nghị</div><div className="mt-1 font-mono">{selected.trigger_price == null ? '--' : selected.trigger_price.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">Khẩu độ hậu kiểm</div><div className="mt-1 font-medium">{selected.is_legacy_group ? 'Ngày tự nhiên (cách cũ)' : 'Phiên giao dịch'}</div></div></div>{(selected.reason || selected.signal) && <div className="space-y-2"><div className="font-medium">Căn cứ lúc đó</div>{selected.signal && <div className="rounded-lg border border-border/60 p-2.5 text-muted-foreground">Tín hiệu:{selected.signal}</div>}{selected.reason && <div className="rounded-lg border border-border/60 p-2.5 leading-relaxed text-muted-foreground">{selected.reason}</div>}</div>}<div className="space-y-2"><div className="font-medium">Kết quả hậu kiểm</div>{['1', '5'].map(horizon => { const outcome = selected.outcomes[horizon]; return <div key={horizon} className="flex items-center justify-between rounded-lg border border-border/60 p-3"><div className="flex items-center gap-2"><Clock3 className="w-3.5 h-3.5 text-muted-foreground" /><span>{horizon} phiên giao dịch</span></div><div className="text-right"><div className={`font-mono ${pctClass(outcome?.return_pct)}`}>{outcome?.status === 'pending' ? 'Chờ điền lại' : formatPct(outcome?.return_pct)}</div><div className="text-[10px] text-muted-foreground">{outcomeLabel(outcome)}</div></div></div> })}</div>{policy && <div className="rounded-lg bg-primary/5 p-3 text-[11px] text-muted-foreground"><div className="mb-1.5 flex items-center gap-1.5 font-medium text-foreground"><CheckCircle2 className="w-3.5 h-3.5 text-primary" />Quy tắc tính trúng</div>{policy.actions[selected.action] || `Khuyến nghị đứng ngoài quan sát: lợi nhuận tuyệt đối nhỏ hơn ${policy.flat_threshold_pct}% thì tính là trúng`}</div>}</div>}</DialogContent></Dialog>
  </div>
}
