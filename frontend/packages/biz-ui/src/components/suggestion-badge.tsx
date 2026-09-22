import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { fetchAPI } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { AiSuggestionBadge } from '@panwatch/biz-ui/components/ai-suggestion-badge'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface SuggestionInfo {
  id?: number
  action: string  // buy/add/reduce/sell/hold/watch
  action_label: string
  signal: string
  reason: string
  should_alert: boolean
  raw?: string
  // Trường mới thêm của kho khuyến nghị
  agent_name?: string     // intraday_monitor/daily_report/premarket_outlook
  agent_label?: string    // Theo dõi trong phiên/nhật báo sau phiên/phân tích trước phiên
  created_at?: string     // Dấu thời gian ISO
  is_expired?: boolean    // Đã hết hạn hay chưa
  prompt_context?: string // Ngữ cảnh Prompt
  ai_response?: string    // Phản hồi gốc của AI
  meta?: Record<string, any>
}

export interface KlineSummary {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  trend: string
  macd_status: string
  macd_cross?: string
  macd_cross_days?: number
  recent_5_up: number
  change_5d: number | null
  change_20d: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60?: number | null
  // RSI
  rsi6?: number | null
  rsi_status?: string
  // KDJ
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  // Dải Bollinger
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_status?: string
  // Khối lượng
  volume_ratio?: number | null
  volume_trend?: string
  // Biên độ dao động
  amplitude?: number | null
  // Hỗ trợ kháng cự nhiều mức
  support: number | null
  resistance: number | null
  support_s?: number | null
  support_m?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  // Hình mẫu nến
  kline_pattern?: string
}

interface SuggestionBadgeProps {
  suggestion: SuggestionInfo | null
  stockName?: string
  stockSymbol?: string
  kline?: KlineSummary | null
  showFullInline?: boolean  // Có hiện đủ thông tin ngay trên dòng hay không (chế độ Dashboard)
  market?: string           // Thị trường (dùng cho hộp thoại chỉ báo kỹ thuật)
  hasPosition?: boolean     // Có đang nắm giữ hay không (dùng cho hộp thoại chỉ báo kỹ thuật)
  showTechnicalCompanion?: boolean // Có hiện phù hiệu đối chiếu chỉ báo kỹ thuật hay không
}

// Định dạng thời gian khuyến nghị (tự đổi sang múi giờ địa phương, chỉ hiện giờ:phút)
function formatSuggestionTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    // Kiểm tra ngày có hợp lệ không
    if (isNaN(date.getTime())) return ''
    // Hiện theo múi giờ địa phương
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

// Định dạng ngày giờ đầy đủ (múi giờ địa phương)
function formatSuggestionDateTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

function formatKlineMeta(meta?: Record<string, any>): string {
  if (!meta) return ''
  const computedAt = meta?.kline_meta?.computed_at
  const asof = meta?.kline_meta?.asof
  const parts: string[] = []
  if (asof) parts.push(`Nến tính tới ${asof}`)
  if (computedAt) parts.push(`计算 ${formatSuggestionTime(computedAt)}`)
  return parts.join(' · ')
}

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false,
  market = 'CN',
  hasPosition = false,
  showTechnicalCompanion = true,
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [feedback, setFeedback] = useState<'useful' | 'useless' | null>(null)
  const { toast } = useToast()

  useEffect(() => {
    setFeedback(null)
  }, [suggestion?.id])

  const canFeedback = !!suggestion?.id && suggestion?.agent_label !== 'Chỉ báo kỹ thuật'
  const submitFeedback = async (useful: boolean) => {
    if (!suggestion?.id) return
    try {
      await fetchAPI('/feedback', {
        method: 'POST',
        body: JSON.stringify({ suggestion_id: suggestion.id, useful }),
      })
      setFeedback(useful ? 'useful' : 'useless')
      toast('Đã gửi phản hồi', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Phản hồi thất bại', 'error')
    }
  }

  const onDialogOpenChange = (open: boolean) => {
    setDialogOpen(open)
    if (!open) {
      try {
        ;(window as any).__panwatch_suppress_card_click_until = Date.now() + 600
      } catch {
        // ignore
      }
    }
  }

  if (!suggestion && !kline) return null

  // Chế độ Dashboard: hiện đủ thông tin ngay trên dòng (chỉ badge khuyến nghị)
  if (showFullInline) {
    if (!suggestion) return null
    const isAI = !!suggestion.agent_name && suggestion.agent_label !== 'Chỉ báo kỹ thuật'
    const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
    const timeStr = formatSuggestionTime(suggestion.created_at)
    const klineMetaStr = formatKlineMeta(suggestion.meta)
    return (
      <>
        <div className="pt-3 border-t border-border/30">
          <div className="flex items-start gap-3">
            <div className="shrink-0 flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="lg"
                onClick={(e) => {
                  e.stopPropagation()
                  if (suggestion.agent_label === 'Chỉ báo kỹ thuật') setKlineDialogOpen(true)
                  else setDialogOpen(true)
                }}
                title="Bấm để xem chi tiết khuyến nghị"
              />
              {isAI && showTechnicalCompanion && (
                <TechnicalBadge
                  label={tech ? tech.action_label : 'Quan sát'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="lg"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="Bấm để xem chi tiết mặt kỹ thuật"
                />
              )}
            </div>
            <div className="flex-1 min-w-0">
              {suggestion.signal && (
                <p className="text-[12px] font-medium text-foreground mb-0.5">{suggestion.signal}</p>
              )}
              {suggestion.reason ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.reason}</p>
              ) : suggestion.raw && !suggestion.signal ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.raw}</p>
              ) : null}

              {(suggestion.agent_label || timeStr) && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  来源: {suggestion.agent_label || (isAI ? 'AI' : 'Không rõ')}
                  {timeStr && ` · ${timeStr}`}
                  {suggestion.is_expired && <span className="ml-1 text-amber-600">(已过期)</span>}
                </div>
              )}

              {klineMetaStr && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  {klineMetaStr}
                </div>
              )}
            </div>
          </div>
        </div>

        <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
          <DialogContent
            className="max-w-md"
            onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AiSuggestionBadge
                  action={suggestion.action}
                  actionLabel={suggestion.action_label}
                  isAI={isAI}
                  isExpired={!!suggestion.is_expired}
                  size="lg"
                />
                {/* Nhãn AI đã đưa lên trước trong chữ của nút, không lặp lại nữa */}
                {stockName && (
                  <span className="text-[14px] font-normal text-muted-foreground">
                    {stockName} {stockSymbol && `(${stockSymbol})`}
                  </span>
                )}
              </DialogTitle>
              {/* Thông tin nguồn */}
              {(suggestion.agent_label || suggestion.created_at) && (
                <div className="text-[11px] text-muted-foreground/70 mt-1">
                  来源: {suggestion.agent_label || 'Không rõ'}
                  {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                  {suggestion.is_expired && <span className="ml-2 text-amber-500">(已过期)</span>}
                </div>
              )}
            </DialogHeader>

            <div className="space-y-4">
              {/* Feedback */}
              {canFeedback && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">这条建议是否有用？</div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => submitFeedback(true)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useful'
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      有用
                    </button>
                    <button
                      onClick={() => submitFeedback(false)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useless'
                          ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      没用
                    </button>
                    {feedback && (
                      <span className="text-[11px] text-muted-foreground">已记录，感谢反馈</span>
                    )}
                  </div>
                </div>
              )}

              {/* Tín hiệu */}
              {suggestion.signal && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                  <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
                </div>
              )}

              {/* Lý do */}
              {(suggestion.reason || suggestion.raw) && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                  <p className="text-[13px] text-foreground">
                    {suggestion.reason || suggestion.raw}
                  </p>
                </div>
              )}

              {/* Chỉ báo kỹ thuật */}
              {kline && (
                <div className="space-y-3">
                  <div className="text-[11px] text-muted-foreground">Chỉ báo kỹ thuật</div>
                  <KlineIndicators summary={kline as any} />
                </div>
              )}

              {/* Phản hồi gốc của AI */}
              {suggestion.ai_response && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">AI 响应</div>
                  <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto scrollbar">
                    {suggestion.ai_response}
                  </div>
                </div>
              )}

              {/* Ngữ cảnh Prompt */}
              {suggestion.prompt_context && (
                <details className="group">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                    Prompt 上下文 <span className="text-[10px]">(bấm để mở)</span>
                  </summary>
                  <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto scrollbar">
                    {suggestion.prompt_context}
                  </div>
                </details>
              )}
            </div>
          </DialogContent>
        </Dialog>
        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  // Chỉ hiện chỉ báo kỹ thuật (không có khuyến nghị)
  if (!suggestion && kline) {
    return (
      <>
        <div className="inline-flex flex-col items-start gap-0.5">
          <TechnicalBadge
            label="Chỉ báo"
            tone="neutral"
            size="xs"
            onClick={(e) => {
              e.stopPropagation()
              setKlineDialogOpen(true)
            }}
            title="Bấm để xem chỉ báo kỹ thuật"
          />
        </div>

        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market || 'CN'}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  if (!suggestion) return null
  const isAI = !!suggestion.agent_name && suggestion.agent_label !== 'Chỉ báo kỹ thuật'

  // Chế độ trang vị thế: phù hiệu nhỏ + bấm mở hộp thoại
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <div className="inline-flex items-center gap-1">
          <AiSuggestionBadge
            action={suggestion.action}
            actionLabel={suggestion.action_label}
            isAI={isAI}
            isExpired={!!suggestion.is_expired}
            size="md"
            onClick={(e) => {
              e.stopPropagation()
              if (suggestion.agent_label === 'Chỉ báo kỹ thuật') setKlineDialogOpen(true)
              else setDialogOpen(true)
            }}
            title={sourceInfo ? `${sourceInfo} - 点击查看详情` : 'Bấm để xem chi tiết khuyến nghị'}
          />
          {showTechnicalCompanion && suggestion.agent_label !== 'Chỉ báo kỹ thuật' && (
            (() => {
              const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
              return (
                <TechnicalBadge
                  label={tech ? tech.action_label : 'Quan sát'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="md"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="Bấm để xem chi tiết mặt kỹ thuật"
                />
              )
            })()
          )}
        </div>
        {/* Nguồn và thời gian (hiện dưới phù hiệu, chỉ với khuyến nghị AI để dễ phân biệt) */}
        {isAI && (
          <div className="mt-1 text-[10px] text-muted-foreground/70">
            来源: {suggestion.agent_label || 'AI'}{timeStr && ` · ${timeStr}`}
            {suggestion.is_expired && <span className="ml-1 text-amber-600">(已过期)</span>}
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
        <DialogContent
          className="max-w-md"
          onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onClick={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="md"
              />
              {stockName && (
                <span className="text-[14px] font-normal text-muted-foreground">
                  {stockName} {stockSymbol && `(${stockSymbol})`}
                </span>
              )}
            </DialogTitle>
            {/* Thông tin nguồn */}
            {(suggestion.agent_label || suggestion.created_at) && (
              <div className="text-[11px] text-muted-foreground/70 mt-1">
                来源: {suggestion.agent_label || 'Không rõ'}
                {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                {suggestion.is_expired && <span className="ml-2 text-amber-500">(已过期)</span>}
              </div>
            )}
          </DialogHeader>

          <div className="space-y-4">
            {/* Feedback */}
            {canFeedback && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">这条建议是否有用？</div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => submitFeedback(true)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useful'
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    有用
                  </button>
                  <button
                    onClick={() => submitFeedback(false)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useless'
                        ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    没用
                  </button>
                  {feedback && (
                    <span className="text-[11px] text-muted-foreground">已记录，感谢反馈</span>
                  )}
                </div>
              </div>
            )}

            {/* Tín hiệu */}
            {suggestion.signal && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">信号</div>
                <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
              </div>
            )}

            {/* Lý do */}
            {(suggestion.reason || suggestion.raw) && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                <p className="text-[13px] text-foreground">
                  {suggestion.reason || suggestion.raw}
                </p>
              </div>
            )}

            {/* Chỉ báo kỹ thuật */}
            {kline && (
              <div className="space-y-3">
                <div className="text-[11px] text-muted-foreground">Chỉ báo kỹ thuật</div>
                <KlineIndicators summary={kline as any} />
              </div>
            )}

            {/* Phản hồi gốc của AI */}
            {suggestion.ai_response && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">AI 响应</div>
                <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto">
                  {suggestion.ai_response}
                </div>
              </div>
            )}

            {/* Ngữ cảnh Prompt */}
            {suggestion.prompt_context && (
              <details className="group">
                <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                  Prompt 上下文 <span className="text-[10px]">(bấm để mở)</span>
                </summary>
                <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto">
                  {suggestion.prompt_context}
                </div>
              </details>
            )}
          </div>
        </DialogContent>
      </Dialog>
      {/* Always mount K-line dialog for technical details */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={stockSymbol || ''}
        market={market}
        stockName={stockName}
        hasPosition={hasPosition}
        initialSummary={kline as any}
      />
    </>
  )
}
