/**
 * Hộp thoại phân tích chuyên sâu (TradingAgents).
 *
 * Ba trạng thái:
 * 1. Đang kích hoạt — hiện «phân tích mất 3-5 phút, bắt đầu chứ?» + ước tính chi phí
 * 2. Đang chạy — hỏi vòng /agents/runs/{trace_id}/progress, hiện tiến độ từng chặng
 * 3. Xong — tóm tắt ở trên + suy luận Markdown + 4 báo cáo chuyên viên mở ra được + tranh luận
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import {
  subscribeSSE,
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressDataSource,
  type ProgressStage,
} from '@panwatch/api'
import {
  isTerminalProgressStatus,
  shouldContinueProgressWatch,
} from '../../../../src/lib/tradingagents-progress'

const STAGE_LABEL: Record<string, string> = {
  data_collection: 'Chuẩn bị dữ liệu',
  market_analyst: 'Chuyên viên phân tích kỹ thuật',
  social_analyst: 'Chuyên viên phân tích tâm lý',
  news_analyst: 'Chuyên viên phân tích tin tức',
  fundamentals_analyst: 'Chuyên viên phân tích cơ bản',
  bull_bear_debate: 'Tranh luận xem tăng xem giảm',
  research_manager: 'Trưởng nhóm nghiên cứu',
  trader: 'Quyết định của trader',
  risk_judge: 'Phán quyết kiểm soát rủi ro',
  final_decision: 'PM tổng hợp',
}

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const POLL_INTERVAL_MS = 2000

/** localStorage ghi trace_id của lần kích hoạt gần nhất trên một mã; đóng rồi mở lại hộp thoại thì khôi phục hỏi vòng */
const STORAGE_KEY_PREFIX = 'panwatch:tradingagents:running:'
/** trace_id sống bao lâu thì coi là có thể đã không còn chạy (tránh hiện idle của một trace cũ) */
const TRACE_MAX_AGE_MS = 60 * 60 * 1000  // Giữ khớp với cửa sổ vòng đời running của backend và chừa dư để khôi phục

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* Bỏ qua các lỗi kiểu quota */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** Phân tích lịch sử (nếu có thì hiện thẳng) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Hàm hủy đăng ký SSE (tiến độ ưu tiên đi qua SSE, hỏng thì hạ xuống hỏi vòng)
  const sseCloseRef = useRef<(() => void) | null>(null)

  /** Ngừng mọi việc nghe tiến độ (SSE + hỏi vòng) */
  const stopWatching = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (sseCloseRef.current) {
      sseCloseRef.current()
      sseCloseRef.current = null
    }
  }, [])

  // Dọn việc nghe tiến độ khi đóng hộp thoại
  useEffect(() => {
    if (!open) stopWatching()
  }, [open, stopWatching])

  // Đặt lại trạng thái ban đầu + hỏi backend xem có tác vụ đang chạy/đã xong không
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // Đặt về idle trước (tránh state lần trước còn sót), rồi hỏi backend bất đồng bộ
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // Hỏi song song 3 nguồn dữ liệu:
    //   - findRunning: mã này trong 30 phút gần nhất có tác vụ nào đang chạy không
    //   - getLatestForStock: có kết quả nào đã xong trong ngày không (quá 30 phút vẫn tính)
    //   - getBudget: ngân sách tháng này (hiện ở trạng thái idle)
    // Thứ tự ưu tiên: running > done (đã có kết quả) > idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget().catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // Thứ tự ưu tiên: running (đang chạy thật) > done (đệm trong ngày, cho phép phân tích lại) > idle
      //   - stale / failed / success / none đều coi là "không chạy"
      //   - Ở trạng thái nào cũng vậy, chỉ cần có đệm trong ngày là hiện DoneView (kèm nút «bỏ đệm, phân tích lại»)
      //   - Ở trạng thái nào cũng vậy, nút «bắt đầu phân tích» của IdleView luôn bấm được, backend sẽ lo chống trùng

      // 1) Đang chạy thật (backend là nguồn có thẩm quyền) → vào running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        // Backend xác nhận đang chạy; dù chặng thu thập tạm thời chưa có nhật ký, vẫn để SSE/hỏi vòng chạy tiếp
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        startWatching(tid)
        return
      }

      // 2) Backend báo stale/failed → tác vụ cũ chết/hỏng, dọn dấu vết cục bộ, đi tiếp sang bước xét đệm
      //    Không quay lại running nữa, cho phép người dùng kích hoạt lại
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
        clearRunningTrace(stockSymbol)
      }

      // 3) Lưới hứng localStorage (vừa kích hoạt xong backend chưa kịp ghi log) — chỉ thử khi backend trả 'none'
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          startWatching(localTrace)
          return
        }
      }

      // 4) Có kết quả đã xong trong ngày → dùng khung done (người dùng bấm «bỏ đệm, phân tích lại» được)
      if (latestResult) {
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 5) Không có gì cả → idle (nút bắt đầu phân tích bấm được, backend đã chống trùng)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol])

  /** Xử lý một ảnh chụp tiến độ (SSE đẩy và hỏi vòng dùng chung một máy trạng thái) */
  const handleProgressResponse = useCallback(
    async (resp: ProgressResponse) => {
      setProgress(resp)
      if (resp.status === 'success') {
        // Xong, kéo kết quả lịch sử về
        stopWatching()
        clearRunningTrace(stockSymbol)
        const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
        if (latest) {
          setResult(latest)
          setStage('done')
        } else {
          setError('Kết quả chưa ghi xuống kho, xin xem lại ở «Lịch sử AI» sau')
          setStage('error')
        }
      } else if (resp.status === 'failed') {
        stopWatching()
        clearRunningTrace(stockSymbol)
        setError(resp.run?.error || 'Phân tích thất bại')
        setStage('error')
      } else if (resp.status === 'stale') {
        // Backend phát hiện running treo (quá cửa sổ vòng đời của cả tác vụ)
        // → tự đưa về idle, người dùng kích hoạt lại được
        stopWatching()
        clearRunningTrace(stockSymbol)
        setTraceId('')
        setProgress(null)
        setStage('idle')
      } else if (resp.status === 'not_found') {
        // SSE/hỏi vòng tạm thời không có ảnh chụp không có nghĩa là tác vụ không tồn tại; bản ghi running ở backend có thể vẫn đang thu thập.
        // Giữ trace lại, để vòng hỏi kế tiếp hoặc lần tải lại trang tiếp quản.
        return
      }
    },
    [stockSymbol, stopWatching],
  )

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        await handleProgressResponse(resp)
      } catch (e) {
        // Hỏi vòng thất bại thì đừng dừng ngay, chỉ ghi một lần lỗi
        console.warn('progress poll error:', e)
      }
    },
    [handleProgressResponse],
  )

  /** Phương án hạ cấp: hỏi vòng bằng setInterval (khi SSE không dùng được) */
  const startPolling = useCallback(
    (tid: string) => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
      void pollProgress(tid)
    },
    [pollProgress],
  )

  /** Bắt đầu nghe tiến độ: ưu tiên SSE (server đẩy), hỏng/đóng luồng thì hạ xuống hỏi vòng (mã hỏi vòng vẫn giữ để hứng) */
  const startWatching = useCallback(
    (tid: string) => {
      stopWatching()
      let terminal = false
      sseCloseRef.current = subscribeSSE(`/agents/runs/${tid}/progress/stream`, {
        onEvent: (ev) => {
          if (ev.event === 'progress' && ev.data && typeof ev.data === 'object') {
            const resp = ev.data as ProgressResponse
            if (isTerminalProgressStatus(resp.status)) terminal = true
            void handleProgressResponse(resp)
          } else if (ev.event === 'done' && ev.data?.status && ev.data.status !== 'timeout') {
            terminal = !shouldContinueProgressWatch(ev.data.status, 'done')
            // Sự kiện done chỉ mang trạng thái, không mang đủ run/result; tới trạng thái cuối vẫn phải kéo thêm một ảnh chụp,
            // tránh cảnh bản progress cuối bị proxy bỏ rơi làm hộp thoại kẹt ở running.
            if (terminal) void pollProgress(tid)
          }
        },
        onClosed: () => {
          // Server đóng luồng bình thường: trạng thái cuối thì kết thúc; chưa cuối (như luồng hết giờ) thì hạ xuống hỏi vòng tiếp sức
          if (!terminal) startPolling(tid)
        },
        onFailed: () => {
          // SSE không dùng được (proxy cũ đệm lại/vấn đề mạng) → hạ xuống hỏi vòng
          startPolling(tid)
        },
      })
    },
    [handleProgressResponse, pollProgress, startPolling, stopWatching],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // Backend không trả trace_id, chỉ hiện message
        setStage('done')
        toast(triggerResp.message || 'Đã kích hoạt', 'success')
        return
      }
      // Lưu bền trace_id để đóng rồi mở lại vẫn khôi phục được tiến độ
      saveRunningTrace(stockSymbol, tid)
      // Khởi động việc nghe tiến độ (ưu tiên SSE, hỏng thì hạ xuống hỏi vòng)
      startWatching(tid)
      // Kéo ngay một lần, để tiến độ ban đầu hiện ra sớm nhất
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : 'Kích hoạt thất bại')
    }
  }, [stockId, stockSymbol, startWatching, pollProgress, toast])

  const handleClose = useCallback(() => {
    stopWatching()
    onOpenChange(false)
  }, [onOpenChange, stopWatching])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 深度分析 · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            Khung quyết định nhiều Agent TradingAgents · chỉ để học hỏi nghiên cứu tham khảo, không phải khuyến nghị đầu tư
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={stockSymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">Phân tích thất bại</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>Đóng</Button>
              <Button onClick={() => handleStart(false)}>Thử lại</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">即将分析:{stockSymbol}</div>
        <div className="text-muted-foreground">
          Gọi 4 nhóm chuyên viên phân tích (kỹ thuật / tâm lý / tin tức / cơ bản) + tranh luận xem tăng xem giảm + kiểm soát rủi ro + PM tổng hợp
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ Ước tính mất: 3-8 phút</div>
          {est ? (
            <div>💰 预估成本:${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 Ước tính chi phí: đang tải...</div>
          )}
          <div>ℹ️ Chạy bất đồng bộ, đóng hộp thoại được, xong sẽ đẩy thông báo qua kênh đã cấu hình</div>
        </div>
      </div>

      {/* Ngân sách tháng này */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">Ngân sách tháng này</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} lượt`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ Ngân sách tháng này đã cạn. Muốn chạy tiếp, xin vào «Cài đặt → Agent → TradingAgents» nâng `monthly_budget_usd`.
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>Hủy</Button>
        <Button onClick={onStart} disabled={overBudget}>Bắt đầu phân tích</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">Đang phân tích...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            已用 {formatElapsed(elapsed)} · ${cost.toFixed(4)}
          </span>
        </div>
        {progress?.active_operation && (
          <div className="text-[11px] text-muted-foreground">
            {progress.active_operation.agent && (
              <>
                Agent hiện tại:<span className="font-mono">{progress.active_operation.agent}</span> ·{' '}
              </>
            )}
            当前操作：{progress.active_operation.kind === 'tool' ? 'Công cụ dữ liệu ' : ''}
            <span className="font-mono">{progress.active_operation.name}</span>
          </div>
        )}
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">Đang chuẩn bị...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>

      <ToolkitDiagnostics
        summary={progress?.toolkit_summary}
        recent={progress?.toolkit_recent || []}
      />

      {progress?.data_sources && progress.data_sources.length > 0 && (
        <DataCollectionDiagnostics sources={progress.data_sources} />
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          Chạy nền (xong sẽ đẩy thông báo)
        </Button>
      </div>
    </div>
  )
}

function DataCollectionDiagnostics({ sources }: { sources: ProgressDataSource[] }) {
  const labels: Record<string, string> = {
    quote: 'Bảng giá',
    klines: 'Nến',
    capital_flow: 'Dòng tiền',
    events: 'Sự kiện',
    financial: 'Báo cáo tài chính',
    technical: 'Chỉ báo kỹ thuật',
  }
  const statusLabels: Record<ProgressDataSource['status'], string> = {
    pending: 'Chờ',
    running: 'Đang yêu cầu',
    done: 'Xong',
    error: 'Hỏng, đã hạ cấp',
  }
  const statusClasses: Record<ProgressDataSource['status'], string> = {
    pending: 'text-muted-foreground',
    running: 'text-sky-600 dark:text-sky-400',
    done: 'text-emerald-600 dark:text-emerald-400',
    error: 'text-amber-600 dark:text-amber-400',
  }

  return (
    <div className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]">
      <div className="font-medium mb-1">Chi tiết chuẩn bị dữ liệu</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {sources.map((source) => (
          <span key={source.name} className={statusClasses[source.status]} title={source.error}>
            {labels[source.name] || source.name}: {statusLabels[source.status]}
          </span>
        ))}
      </div>
    </div>
  )
}

interface ToolkitDiagItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface ToolkitDiagSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}

export function ToolkitDiagnostics({
  summary,
  recent,
  defaultOpen = false,
}: {
  summary: ToolkitDiagSummary | undefined
  recent: ToolkitDiagItem[]
  defaultOpen?: boolean
}) {
  if (!summary && recent.length === 0) return null

  const hit = summary?.hit ?? 0
  const miss = summary?.miss ?? 0
  const pass = summary?.passthrough ?? 0
  const fall = summary?.fallthrough ?? 0
  const err = summary?.error ?? 0
  const total = hit + miss + pass + fall + err

  const ACTION_CLS: Record<string, string> = {
    HIT: 'text-emerald-600 dark:text-emerald-400',
    MISS: 'text-amber-600 dark:text-amber-400',
    PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
    FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
    ERROR: 'text-rose-600',
  }

  return (
    <details className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]" open={defaultOpen}>
      <summary className="cursor-pointer flex items-center gap-2 flex-wrap">
        <span className="font-medium">Chẩn đoán việc nạp dữ liệu</span>
        <span className="text-[11px] text-muted-foreground">
          (dữ liệu PanWatch → công cụ TradingAgents)
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · 透传 {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · 兜底 {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · 错误 {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: 用 PanWatch 数据 ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: 命中但 PanWatch 未实现 ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>Chuyển thẳng</span>: 非 A 股直接走上游 vendor ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>Lưới hứng</span>: cổ phiếu A nhưng cache rỗng, đã đi lên thượng nguồn
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          ⚠️ Chưa có bản ghi gọi công cụ nào (có thể TradingAgents vẫn đang ở chặng chuẩn bị).
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {recent.map((h, i) => {
            const action = (h.action || '').toUpperCase()
            const row = (
              <div className="font-mono text-[10.5px] flex items-center gap-2 hover:bg-accent/30 px-1 rounded cursor-help w-full">
                <span className={`${ACTION_CLS[action] || 'text-muted-foreground'} w-20 shrink-0`}>
                  {action}
                </span>
                <span className="text-foreground/80 truncate flex-1 text-left">
                  {h.method} ({h.symbol || '-'})
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                  {h.chars != null && <span className="text-muted-foreground"> · {h.chars} 字符</span>}
                  {h.source && <span className="text-muted-foreground/70"> · {h.source}</span>}
                </span>
              </div>
            )
            const hasDetail = !!(h.snippet || h.reason)
            if (!hasDetail) return <div key={i}>{row}</div>
            return (
              <HoverPopover
                key={i}
                className="block w-full"
                trigger={row}
                title={
                  <span>
                    <span className={ACTION_CLS[action] || 'text-muted-foreground'}>{action}</span>
                    <span className="text-muted-foreground"> · {h.method}({h.symbol || '-'})</span>
                    {h.source && (
                      <span className="text-muted-foreground/70"> · {h.source}</span>
                    )}
                  </span>
                }
                content={
                  <div className="space-y-2">
                    {h.reason && (
                      <div className="text-[11px] text-amber-600 dark:text-amber-400">
                        {h.reason}
                      </div>
                    )}
                    {h.snippet && (
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}...(共 {h.chars} 字符,仅展示前 {h.snippet.length})
                          </span>
                        )}
                      </pre>
                    )}
                  </div>
                }
                popoverClassName="w-[44rem] max-w-[90vw]"
                side="top"
                align="start"
              />
            )
          })}
        </div>
      )}
    </details>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-primary'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  stockSymbol,
  onRerun,
}: {
  result: DeepAnalysisResult
  stockSymbol: string
  onRerun: () => void
}) {
  // Giá trị mặc định phòng thủ: lúc backend kéo lịch sử, raw_data có thể thiếu, ở đây cho fallback đầy đủ để khỏi trắng màn hình
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: 'Nắm giữ',
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: 'TradingAgents chuyên sâu',
    confidence: 5.0,
  }
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd
  const sections = buildAnalysisSections(rawData)
  const analysisDate = result.timestamp
    ? String(result.timestamp).slice(0, 10)
    : new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ Đệm trong ngày: hôm nay đã phân tích mã này rồi, đang hiện kết quả đệm (không tốn thêm chi phí)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            Bỏ đệm, phân tích lại
          </Button>
        </div>
      )}

      {/* Tóm tắt trên cùng (gọn thành một dòng: quyết định + độ tin cậy + chi phí; lý do đầy đủ nằm ở tab "Quyết định cuối") */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        <span className={`text-[18px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
          {sug.action_label}
        </span>
        <span className="text-[12px] text-muted-foreground">
          置信度 {sug.confidence?.toFixed(1) ?? '-'} / 10
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          onClick={() => window.open(`/analysis/${stockSymbol}/${analysisDate}`, '_blank')}
        >
          Xem trang chi tiết
        </Button>
        <span className="text-[10px] text-muted-foreground">
          成本:${costUsd?.toFixed(4) ?? '-'}
        </span>
      </div>

      {/* Tab thống nhất: quyết định cuối + bốn chuyên viên phân tích + tranh luận xem tăng xem giảm + tranh luận kiểm soát rủi ro (đầy đủ + bảng GFM) */}
      <AnalysisTabs sections={sections} />

      {/* Chẩn đoán việc nạp dữ liệu (báo cáo lịch sử): lấy từ raw_data.toolkit_diagnostic */}
      {rawData.toolkit_diagnostic && (
        <ToolkitDiagnostics
          summary={rawData.toolkit_diagnostic.summary}
          recent={rawData.toolkit_diagnostic.recent || []}
        />
      )}

      {/* Miễn trừ trách nhiệm */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        Bản phân tích này do khung nhiều Agent AI dựng ra, chỉ để học hỏi nghiên cứu tham khảo, không phải khuyến nghị đầu tư.
        Đầu tư có rủi ro, quyết định phải tự cân nhắc.
      </div>
    </div>
  )
}

/** Tab thống nhất cho quyết định và phân tích. Nội dung do buildAnalysisSections ghép (hộp thoại và trang chi tiết dùng chung), chỉ dựng những tab có nội dung. */
function AnalysisTabs({ sections }: { sections: AnalysisSection[] }) {
  if (sections.length === 0) return null
  return (
    <div className="rounded-lg border border-border/50 p-4">
      <Tabs defaultValue={sections[0].id}>
        <TabsList>
          {sections.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {sections.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-table:my-3 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-table:text-[12px] prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.markdown}</ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
