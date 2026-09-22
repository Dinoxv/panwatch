import { useCallback, useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface KlineSummaryData {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  last_close?: number | null
  recent_5_up?: number | null
  trend?: string
  macd_status?: string
  macd_cross?: string | null
  macd_cross_days?: number | null
  macd_hist?: number | null
  rsi6?: number | null
  rsi_status?: string
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  volume_ratio?: number | null
  volume_trend?: string
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_width?: number | null
  boll_status?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  kline_pattern?: string | null
  support?: number | null
  resistance?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
  change_5d?: number | null
  change_20d?: number | null
  amplitude?: number | null
  amplitude_avg5?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface KlineSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  initialSummary?: KlineSummaryData | null
}

function formatLocalDateTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

export function KlineSummaryDialog({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  hasPosition,
  initialSummary = null,
}: KlineSummaryDialogProps) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const buildSuggestion = (s: KlineSummaryData, holding?: boolean) => {
    const scored = buildKlineSuggestion(s, holding)
    const items: Array<{ text: string; delta: number }> = []
    let localScore = 0

    const add = (text: string, delta: number) => { items.push({ text, delta }); localScore += delta }

    if (s.trend?.includes('多头')) add('Các đường trung bình xếp tăng, xu thế thiên mạnh', 2)
    else if (s.trend?.includes('空头')) add('Các đường trung bình xếp giảm, xu thế thiên yếu', -2)

    if (s.macd_status?.includes('金叉')) add('MACD cắt lên, động lượng ngắn hạn thiên mạnh', 2)
    if (s.macd_status?.includes('死叉')) add('MACD cắt xuống, động lượng ngắn hạn chuyển yếu', -2)
    if (typeof s.macd_hist === 'number') add(`Thanh MACD ${s.macd_hist > 0 ? 'dương' : s.macd_hist < 0 ? 'âm' : 'gần 0'}`, s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0)

    if (s.rsi_status?.includes('超卖')) add('RSI quá bán, có thể bật lại', 1)
    else if (s.rsi_status?.includes('偏强')) add('RSI mạnh, bên mua chiếm ưu thế', 1)
    else if (s.rsi_status?.includes('超买')) add('RSI quá mua, coi chừng nhịp điều chỉnh', -1)
    else if (s.rsi_status?.includes('偏弱')) add('RSI yếu, ngắn hạn chịu áp lực', -1)

    if (s.kdj_status?.includes('金叉')) add('KDJ cắt lên, ngắn hạn chuyển mạnh', 1)
    if (s.kdj_status?.includes('死叉')) add('KDJ cắt xuống, ngắn hạn chuyển yếu', -1)

    if (s.boll_status?.includes('突破上轨')) add('Vượt dải Bollinger trên, xu thế mạnh', 1)
    else if (s.boll_status?.includes('跌破下轨')) add('Thủng dải Bollinger dưới, diễn biến thiên yếu', -1)

    if (s.volume_trend?.includes('放量')) add('Khối lượng bùng lên đồng thuận, dòng tiền tham gia nhiều hơn', 1)
    else if (s.volume_trend?.includes('缩量')) add('Khối lượng cạn, thiếu động lượng', -1)

    if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('Giá sát vùng hỗ trợ, xác suất chặn đà giảm rồi bật lại tăng lên', 1)
    if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('Giá sát vùng kháng cự, dư địa đi lên bị chặn', -1)

    return { ...scored, score: localScore, items }
  }

  useEffect(() => {
    if (!open || !symbol) return

    // If we already have preloaded summary, use it without refetch
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setSummary(null)

    const m = market || 'CN'
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(m)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = effectiveSummary ? buildSuggestion(effectiveSummary, hasPosition) : null

  const handleAskAI = useCallback(() => {
    if (!effectiveSummary) return
    const s = effectiveSummary
    const parts: string[] = []
    const items = []
    if (s.trend) items.push(`Xu thế ${s.trend}`)
    if (s.macd_status) items.push(`MACD${s.macd_status}${s.macd_hist != null ? `(hist=${s.macd_hist.toFixed(3)})` : ''}`)
    if (s.rsi_status) items.push(`RSI${s.rsi_status}${s.rsi6 != null ? `(${s.rsi6.toFixed(0)})` : ''}`)
    if (s.kdj_status) items.push(`KDJ${s.kdj_status}${s.kdj_k != null ? `(K=${s.kdj_k.toFixed(1)},D=${s.kdj_d?.toFixed(1)},J=${s.kdj_j?.toFixed(1)})` : ''}`)
    if (s.boll_status) items.push(`Bollinger ${s.boll_status}${s.boll_width != null ? `(bề rộng dải ${s.boll_width.toFixed(1)}%)` : ''}`)
    if (s.volume_trend) items.push(`Khối lượng ${s.volume_trend}${s.volume_ratio != null ? `(${s.volume_ratio.toFixed(1)}x)` : ''}`)
    if (items.length) parts.push(`Chỉ báo kỹ thuật: ${items.join(', ')}`)
    if (s.support != null) parts.push(`Vùng hỗ trợ: ${s.support.toFixed(2)}`)
    if (s.resistance != null) parts.push(`Vùng kháng cự: ${s.resistance.toFixed(2)}`)
    if (s.last_close != null) parts.push(`Giá đóng cửa: ${s.last_close.toFixed(2)}`)
    if (s.change_5d != null) parts.push(`Tăng giảm 5 ngày: ${s.change_5d.toFixed(2)}%`)
    if (s.change_20d != null) parts.push(`Tăng giảm 20 ngày: ${s.change_20d.toFixed(2)}%`)
    if (s.ma5 != null) parts.push(`Đường trung bình: MA5=${s.ma5.toFixed(2)} MA10=${s.ma10?.toFixed(2)} MA20=${s.ma20?.toFixed(2)} MA60=${s.ma60?.toFixed(2)}`)
    if (suggestion) {
      parts.push(`Điểm kỹ thuật: ${suggestion.action_label}(score=${suggestion.score}), tín hiệu: ${suggestion.signal || 'Trung tính'}`)
      if (suggestion.items.length) {
        parts.push(`Căn cứ chấm điểm: ${suggestion.items.map(e => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('; ')}`)
      }
    }
    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
      detail: { symbol, market, stockName: stockName || symbol, pageContext: parts.join('\n') }
    }))
    onOpenChange(false)
  }, [effectiveSummary, suggestion, symbol, market, stockName, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>Nến / chỉ báo kỹ thuật</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {effectiveSummary?.timeframe ? `Khung: ${effectiveSummary.timeframe}` : 'Khung: 1d'}
                  {effectiveSummary?.asof ? ` · dữ liệu tính tới: ${effectiveSummary.asof}` : ''}
                  {effectiveSummary?.computed_at ? ` · thời điểm tính: ${formatLocalDateTime(effectiveSummary.computed_at)}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">Đang tải...</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">Chưa có dữ liệu</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="p-3 rounded-lg bg-accent/20 border border-border/30">
                <div className="flex items-center justify-between gap-2">
                  <TechnicalBadge
                    label={suggestion.action_label}
                    tone={technicalToneFromSuggestionAction(suggestion.action, suggestion.action_label)}
                    size="sm"
                  />
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? 'Đang nắm giữ' : 'Chưa nắm giữ'} · score {suggestion.score}
                  </span>
                </div>
                <div className="mt-2 text-[12px] text-foreground font-medium">
                  {suggestion.signal}
                </div>

                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((it, idx) => {
                      const color =
                        it.delta > 0 ? 'text-rose-500' :
                        it.delta < 0 ? 'text-emerald-500' :
                        'text-muted-foreground'
                      return (
                        <div key={`${it.text}-${idx}`} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className="text-muted-foreground">{it.text}</span>
                          <span className={`font-mono ${color}`}>
                            {it.delta > 0 ? '+' : ''}{it.delta}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                <div className="mt-2 text-[10px] text-muted-foreground/70">
                  Chỉ dựng từ quy tắc chỉ báo kỹ thuật, không phải khuyến nghị đầu tư
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">
              Mẹo: rê chuột lên nhãn chỉ báo để xem giải thích chi tiết
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary.trend && (
                <HoverPopover
                  title="Xu thế (cách xếp đường trung bình)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Nhãn xu thế lấy từ vị trí tương đối của các đường trung bình (MA5/MA10/MA20), tính trên giá đóng cửa nến ngày. MA càng ngắn càng nhạy, càng dài càng mượt.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Cách đọc thường gặp:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Xếp tăng</span>(MA5 &gt; MA10 &gt; MA20): xu hướng đi lên «thuận» hơn, nhịp chỉnh thường nhìn hỗ trợ ở MA5/MA10 trước.</li>
                          <li><span className="font-medium text-foreground">Xếp giảm</span>(MA5 &lt; MA10 &lt; MA20): xu hướng đi xuống chiếm ưu thế, bật lại tới MA10/MA20 thường gặp kháng cự.</li>
                          <li><span className="font-medium text-foreground">Đường trung bình đan xen</span>: giai đoạn giằng co/sang tay, tín hiệu phụ thuộc nhiều hơn vào khối lượng và các mốc giá then chốt.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Hiện tại:{effectiveSummary.trend}</div>
                      {(effectiveSummary.ma5 != null || effectiveSummary.ma10 != null || effectiveSummary.ma20 != null || effectiveSummary.ma60 != null) && (
                        <div className="text-[10px] text-muted-foreground/70">
                          Đường trung bình: MA5≈{effectiveSummary.ma5 != null ? effectiveSummary.ma5.toFixed(2) : '—'}; MA10≈{effectiveSummary.ma10 != null ? effectiveSummary.ma10.toFixed(2) : '—'}; MA20≈{effectiveSummary.ma20 != null ? effectiveSummary.ma20.toFixed(2) : '—'}; MA60≈{effectiveSummary.ma60 != null ? effectiveSummary.ma60.toFixed(2) : '—'}
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: đường trung bình là chỉ báo trễ, hợp để «lọc xu thế» hơn, không nên dùng riêng làm căn cứ vào ra lệnh.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.trend} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.macd_status && (
                <HoverPopover
                  title="MACD (xu thế/động lượng)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        MACD gồm hai đường (DIF/DEA) và thanh (hist). Khẩu độ thường gặp: DIF=EMA12-EMA26, DEA=EMA(DIF,9), hist≈(DIF-DEA)*2.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Nghĩa là gì:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Cắt lên</span>: DIF cắt lên DEA, động lượng ngắn hạn từ yếu chuyển mạnh.</li>
                          <li><span className="font-medium text-foreground">Cắt xuống</span>: DIF cắt xuống DEA, động lượng ngắn hạn từ mạnh chuyển yếu.</li>
                          <li><span className="font-medium text-foreground">Thanh dương/âm</span>: giá trị dương thường cho thấy động lượng bên mua trội hơn; giá trị âm thường cho thấy động lượng bên bán trội hơn.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Hiện tại: {effectiveSummary.macd_status}{effectiveSummary.macd_hist != null ? `, thanh ${effectiveSummary.macd_hist > 0 ? 'dương' : effectiveSummary.macd_hist < 0 ? 'âm' : 'gần 0'} (hist≈${effectiveSummary.macd_hist.toFixed(3)})` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: trong vùng giằng co, MACD hay «cắt giả» liên tục, thường phải kết hợp xu thế (đường trung bình) và giá-khối lượng để xác nhận.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`MACD ${effectiveSummary.macd_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.rsi_status && (
                <HoverPopover
                  title="RSI (sức mạnh tương đối)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        RSI dùng để đo sức mạnh tương đối giữa lực tăng và lực giảm trong một khoảng thời gian (0-100). Ở đây hiện RSI6 (6 phiên giao dịch gần nhất).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Ngưỡng dùng trong dự án:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>RSI6 &gt; 80: quá mua (rủi ro điều chỉnh cao hơn)</li>
                          <li>RSI6 70-80: thiên mạnh (động lượng nghiêng mua)</li>
                          <li>RSI6 &lt; 20: quá bán (có thể bật lại, nhưng trong xu hướng giảm vẫn quá bán kéo dài được)</li>
                          <li>RSI6 20-30: thiên yếu (động lượng nghiêng bán)</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Hiện tại: {effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null ? `, RSI6≈${effectiveSummary.rsi6.toFixed(0)}` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: quá mua không có nghĩa là giảm ngay, quá bán không có nghĩa là bật lại ngay; cách dùng đáng tin hơn là kết hợp xu thế và mốc then chốt để nhìn «phân kỳ/cạn lực».
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`RSI ${effectiveSummary.rsi_status}${effectiveSummary.rsi6 != null ? ` (${effectiveSummary.rsi6.toFixed(0)})` : ''}`}
                      tone={effectiveSummary.rsi_status === '超买' ? 'bullish' : effectiveSummary.rsi_status === '超卖' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kdj_status && (
                <HoverPopover
                  title="KDJ (chỉ báo ngẫu nhiên)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        KDJ thuộc nhóm chỉ báo động lượng, phản ánh giá đang nằm ở đâu trong một biên độ (giống bộ dao động ngẫu nhiên). Tín hiệu hay dùng là K cắt lên/cắt xuống D.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Nghĩa là gì:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Cắt lên</span>: dấu hiệu ngắn hạn chuyển mạnh, đi cùng xu hướng tăng thì hiệu quả hơn.</li>
                          <li><span className="font-medium text-foreground">Cắt xuống</span>: dấu hiệu ngắn hạn chuyển yếu, đi cùng xu hướng giảm thì hiệu quả hơn.</li>
                          <li>Khi giá trị J cực đoan (&gt;100 hoặc &lt;0) thường được coi là «quá mua/quá bán», nhưng trong xu thế mạnh có thể sai lệch.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Hiện tại: {effectiveSummary.kdj_status}</div>
                        {(effectiveSummary.kdj_k != null || effectiveSummary.kdj_d != null || effectiveSummary.kdj_j != null) && (
                          <div>
                            K≈{effectiveSummary.kdj_k != null ? effectiveSummary.kdj_k.toFixed(1) : '—'}{' '}
                            D≈{effectiveSummary.kdj_d != null ? effectiveSummary.kdj_d.toFixed(1) : '—'}{' '}
                            J≈{effectiveSummary.kdj_j != null ? effectiveSummary.kdj_j.toFixed(1) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: trong thị trường giằng co, KDJ có thể đảo qua đảo lại liên tục, nên dùng kèm vùng hỗ trợ/kháng cự.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`KDJ ${effectiveSummary.kdj_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary?.volume_trend && (
                <HoverPopover
                  title="Khối lượng (bùng/cạn)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Khối lượng dùng để xét diễn biến «có khớp lệnh đỡ hay không». Xu thế khối lượng ở đây lấy từ volume_ratio (khối lượng hôm nay / khối lượng bình quân 5 phiên gần nhất).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Đọc thế nào:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Khối lượng bùng</span>: thường cho thấy mức tham gia tăng lên; nếu tăng giá kèm khối lượng bùng thì càng thuận cho xu thế đi tiếp.</li>
                          <li><span className="font-medium text-foreground">Khối lượng cạn</span>: có thể cho thấy đang đứng ngoài/cạn lực; nếu giảm giá mà khối lượng cạn, đôi khi là dấu hiệu áp lực bán đã nhẹ đi.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Hiện tại: {effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null ? `, tỷ lệ khối lượng≈${effectiveSummary.volume_ratio.toFixed(1)}x` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: ý nghĩa của khối lượng phải xét cùng chiều giá (giá tăng khối lượng tăng / giá tăng khối lượng cạn / giá giảm khối lượng tăng / giá giảm khối lượng cạn) mới đủ.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`${effectiveSummary.volume_trend}${effectiveSummary.volume_ratio != null ? ` (${effectiveSummary.volume_ratio.toFixed(1)}x)` : ''}`}
                      tone={effectiveSummary.volume_trend === '放量' ? 'warning' : effectiveSummary.volume_trend === '缩量' ? 'info' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.boll_status && (
                <HoverPopover
                  title="Dải Bollinger (biến động/kênh giá)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Dải Bollinger gồm dải giữa (thường là MA20) và dải trên dải dưới (dải giữa ±2 lần độ lệch chuẩn), dùng để mô tả kênh giá và mức biến động.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Nghĩa là gì:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Vượt dải trên</span>: ngắn hạn thiên mạnh, nhưng cũng có thể «vọt lên rồi rơi lại», cần khối lượng xác nhận.</li>
                          <li><span className="font-medium text-foreground">Thủng dải dưới</span>: ngắn hạn thiên yếu, nhưng lúc rơi hoảng loạn cũng có thể bật lại do giảm quá đà.</li>
                          <li>Bề rộng dải bóp lại hay gặp khi biến động co cụm, sau đó dễ chọn hướng; bề rộng dải mở ra là biến động phình to.</li>
                        </ul>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Ngưỡng bề rộng dải dùng trong dự án:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Bề rộng &lt; 5: bóp hẹp (thiên về đi ngang/tích lũy)</li>
                          <li>Bề rộng &gt; 15: mở rộng (biến động phình to)</li>
                          <li>Khác: biến động bình thường</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>
                          Hiện tại: {effectiveSummary.boll_status}{effectiveSummary.boll_width != null ? `, bề rộng dải≈${effectiveSummary.boll_width.toFixed(1)}%` : ''}
                        </div>
                        {(effectiveSummary.boll_upper != null || effectiveSummary.boll_mid != null || effectiveSummary.boll_lower != null) && (
                          <div>
                            Dải trên≈{effectiveSummary.boll_upper != null ? effectiveSummary.boll_upper.toFixed(2) : '—'}; dải giữa≈{effectiveSummary.boll_mid != null ? effectiveSummary.boll_mid.toFixed(2) : '—'}; dải dưới≈{effectiveSummary.boll_lower != null ? effectiveSummary.boll_lower.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Bollinger ${effectiveSummary.boll_status}`}
                      tone={effectiveSummary.boll_status === '突破上轨' ? 'bullish' : effectiveSummary.boll_status === '跌破下轨' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kline_pattern && (
                <HoverPopover
                  title="Hình mẫu nến (cấu trúc cục bộ)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Hình mẫu nhận từ hình dạng 1-2 cây nến gần nhất (như doji, nến búa, nến nhấn chìm…), thuộc nhóm «tín hiệu cục bộ».
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Nghĩa là gì:</span>
                        Phần lớn hình mẫu cần xu thế, khối lượng và mốc then chốt xác nhận. Ví dụ nến búa xuất hiện ở cuối nhịp giảm thì có ý nghĩa hơn; nến nhấn chìm thì coi trọng «đối chiếu hai cây nến trước sau».
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Hiện tại:{effectiveSummary.kline_pattern}</div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: hình mẫu một cây nến đơn lẻ sai khá nhiều, nên chỉ coi là gợi ý, không nên quyết định đơn độc.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.kline_pattern} tone="warning" help />
                  }
                />
              )}
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary && effectiveSummary.support != null && (
                <HoverPopover
                  title="Vùng hỗ trợ (vùng hỗ trợ then chốt)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Vùng hỗ trợ có thể hiểu là vùng giá «bên mua dễ xuất hiện hơn». Khi sát hỗ trợ, giá dễ chặn đà giảm, bật lại hoặc đi ngang hơn.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Dự án này tính thế nào:</span>
                        Hỗ trợ (support) trong hộp thoại hiện tại lấy từ giá thấp nhất (min low) trong 20 phiên giao dịch gần nhất, thuộc mốc tham chiếu cỡ trung hạn.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Dùng thế nào:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Nên xem là một «vùng» chứ không phải một đường chính xác tới từng xu, cách làm thường gặp là cho phép sai số nhất định (ví dụ ±1%~2%).</li>
                          <li>Khi sát hỗ trợ, nếu khối lượng cạn mà chặn được đà giảm/khối lượng bùng mà bật lại thì tín hiệu thường đáng tin hơn; nếu thủng xuống kèm khối lượng lớn thì hỗ trợ có thể mất hiệu lực và đổi vai thành kháng cự.</li>
                          <li>Hợp để đặt vùng cắt lỗ/chốt lời/tăng giảm tỷ trọng: dùng mốc then chốt để bó rủi ro, chứ không phải để đoán đỉnh đoán đáy.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Hiện tại: hỗ trợ≈{effectiveSummary.support.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.support > 0 && (
                          <div>
                            Khoảng cách (tính theo giá đóng cửa)≈{(((effectiveSummary.last_close - effectiveSummary.support) / effectiveSummary.support) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close <= effectiveSummary.support * 1.02 ? '(sát hỗ trợ, quy tắc chấm điểm sẽ cộng điểm)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.support_s != null || effectiveSummary.support_m != null || effectiveSummary.support_l != null) && (
                          <div>
                            Nhiều cấp: ngắn hạn (5 ngày)≈{effectiveSummary.support_s != null ? effectiveSummary.support_s.toFixed(2) : '—'}; trung hạn (20 ngày)≈{effectiveSummary.support_m != null ? effectiveSummary.support_m.toFixed(2) : '—'}; dài hạn (60 ngày)≈{effectiveSummary.support_l != null ? effectiveSummary.support_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: hỗ trợ/kháng cự là «mốc then chốt rút ra từ thống kê», không phải điểm chắc chắn sẽ đảo chiều; xu thế mạnh thì xuyên thẳng qua được.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Hỗ trợ ${effectiveSummary.support.toFixed(2)}`}
                      tone="bearish"
                      help
                    />
                  }
                />
              )}
              {effectiveSummary && effectiveSummary.resistance != null && (
                <HoverPopover
                  title="Vùng kháng cự (vùng kháng cự then chốt)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">Là gì:</span>
                        Vùng kháng cự có thể hiểu là vùng giá «bên bán dễ xuất hiện hơn». Khi sát kháng cự, đà đi lên dễ bị chặn, rơi lại hoặc chuyển sang giằng co.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Dự án này tính thế nào:</span>
                        Kháng cự (resistance) trong hộp thoại hiện tại lấy từ giá cao nhất (max high) trong 20 phiên giao dịch gần nhất, thuộc mốc tham chiếu cỡ trung hạn.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Dùng thế nào:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Càng sát kháng cự, mua đuổi càng kém lợi; chiến lược thường gặp hơn là chờ «bứt phá kèm khối lượng rồi kiểm tra lại không thủng» mới cân nhắc.</li>
                          <li>Nếu bứt phá kháng cự kèm khối lượng lớn rồi đứng vững, kháng cự cũ thường «đổi vai» thành hỗ trợ mới.</li>
                          <li>Quanh kháng cự có thể dùng để lên kế hoạch chốt lời/hạ tỷ trọng từng phần, hoặc quan sát xem có phân kỳ giá-khối lượng, vọt lên rồi rơi lại hay các dấu hiệu rủi ro khác không.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Hiện tại: kháng cự≈{effectiveSummary.resistance.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.resistance > 0 && (
                          <div>
                            Khoảng cách (tính theo giá đóng cửa)≈{(((effectiveSummary.resistance - effectiveSummary.last_close) / effectiveSummary.resistance) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close >= effectiveSummary.resistance * 0.98 ? '(sát kháng cự, quy tắc chấm điểm sẽ trừ điểm)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.resistance_s != null || effectiveSummary.resistance_m != null || effectiveSummary.resistance_l != null) && (
                          <div>
                            Nhiều cấp: ngắn hạn (5 ngày)≈{effectiveSummary.resistance_s != null ? effectiveSummary.resistance_s.toFixed(2) : '—'}; trung hạn (20 ngày)≈{effectiveSummary.resistance_m != null ? effectiveSummary.resistance_m.toFixed(2) : '—'}; dài hạn (60 ngày)≈{effectiveSummary.resistance_l != null ? effectiveSummary.resistance_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Lưu ý: cú bứt phá có thật hay không thường phụ thuộc «có khối lượng không + có đứng vững/kiểm tra lại được không». Chỉ xuyên qua trong chốc lát thì rất dễ là bứt phá giả.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Kháng cự ${effectiveSummary.resistance.toFixed(2)}`}
                      tone="bullish"
                      help
                    />
                  }
                />
              )}
            </div>

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && (
                  <HoverPopover
                    title="Biên độ 5 ngày (động lượng ngắn hạn)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">Là gì:</span>
                          Biên độ 5 ngày là tỷ suất lợi nhuận chung của 5 phiên giao dịch gần nhất, dùng để xem nhanh động lượng ngắn hạn mạnh hay yếu.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Dự án này tính thế nào:</span>
                          Lấy «đóng cửa hôm nay» so với «đóng cửa 5 phiên giao dịch trước»: (Close[t]-Close[t-5]) / Close[t-5].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Đọc thế nào:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Giá trị dương: ngắn hạn thiên mạnh; đi cùng khối lượng bùng/xu thế tăng thì càng dễ đi tiếp.</li>
                            <li>Giá trị âm: ngắn hạn thiên yếu; nếu đồng thời đường trung bình xếp giảm, MACD cắt xuống thì rủi ro lớn hơn.</li>
                            <li>Mức tăng dương quá lớn cũng có thể là «nóng quá trong ngắn hạn», phải phòng nhịp điều chỉnh; nên kết hợp vùng hỗ trợ/kháng cự để đặt kiểm soát rủi ro.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Hiện tại: {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        5 ngày{' '}
                        <span className={effectiveSummary.change_5d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.change_20d != null && (
                  <HoverPopover
                    title="Biên độ 20 ngày (động lượng đánh sóng/một tháng)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">Là gì:</span>
                          Biên độ 20 ngày xấp xỉ tỷ suất lợi nhuận của một tháng giao dịch, thiên về thể hiện «xu thế đánh sóng» hơn.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Dự án này tính thế nào:</span>
                          Lấy «đóng cửa hôm nay» so với «đóng cửa 20 phiên giao dịch trước»: (Close[t]-Close[t-20]) / Close[t-20].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Đọc thế nào:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Dương và xu thế xếp tăng: thường thuận chiều hơn; lúc chỉnh thì chú ý vùng hỗ trợ và khối lượng khớp lệnh.</li>
                            <li>Âm và xu thế xếp giảm: thường ngược chiều hơn; bật lại tới gần vùng kháng cự dễ bị chặn.</li>
                            <li>5 ngày và 20 ngày lệch nhau: có thể là «nhịp bật lại/nhịp chỉnh ngắn hạn» nằm trong một xu thế lớn hơn, phải cẩn thận phân biệt có đảo chiều hay không.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Hiện tại: {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        20 ngày{' '}
                        <span className={effectiveSummary.change_20d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.amplitude != null && (
                  <HoverPopover
                    title="Biên độ dao động (cường độ biến động)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">Là gì:</span>
                          Biên độ dao động mô tả khoảng dao động giữa giá cao và giá thấp trong ngày, dùng để đo «biến động lớn tới đâu».
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Dự án này tính thế nào:</span>
                          Biên độ hôm nay≈(High-Low)/Low. Số càng lớn thì trong phiên càng dao động dữ, rủi ro lẫn cơ hội đều lớn hơn.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">Đọc thế nào:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Biên độ cao hay gặp ở bứt phá kèm khối lượng, rơi hoảng loạn, tin tức dẫn dắt…; phải kết hợp khối lượng và xu thế để xét «đang phình ra hay đang sụp».</li>
                            <li>Biên độ thấp hay gặp ở giai đoạn đi ngang/co cụm; nếu dải Bollinger cũng bóp lại thì sau đó càng dễ chọn hướng.</li>
                            <li>Biên độ cao thì nên hạ tỷ trọng/cắt lỗ chặt hơn; tránh dùng «cùng một khoảng cắt lỗ» cho những mức biến động khác nhau.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 space-y-1">
                          <div>Hiện tại: {effectiveSummary.amplitude.toFixed(2)}%</div>
                          {effectiveSummary.amplitude_avg5 != null && (
                            <div>Bình quân 5 phiên gần nhất: {effectiveSummary.amplitude_avg5.toFixed(2)}%</div>
                          )}
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        Biên độ dao động: {effectiveSummary.amplitude.toFixed(2)}%
                      </span>
                    }
                  />
                )}
              </div>
            )}

            <details className="group">
              <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                Giải thích quy tắc khuyến nghị/chấm điểm <span className="text-[10px]">(bấm để mở)</span>
              </summary>
              <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 space-y-2">
                <div className="font-medium text-foreground">Quy tắc khuyến nghị (theo có nắm giữ hay không)</div>
                <div className="space-y-1">
                  <div>Chưa nắm giữ: score ≥ 3 → mua vào; score ≤ -2 → tránh ra; còn lại → đứng ngoài quan sát</div>
                  <div>Đang nắm giữ: score ≥ 3 → gia tăng tỷ trọng; score ≥ 1 → nắm giữ; score ≤ -3 → bán ra; score ≤ -1 → hạ tỷ trọng; còn lại → đứng ngoài quan sát</div>
                </div>
                <div className="font-medium text-foreground">Quy tắc chấm điểm (cộng dồn từng mục, 0 là trung tính)</div>
                <div className="space-y-1">
                  <div>Xu thế (đường trung bình): xếp tăng +2; xếp giảm -2</div>
                  <div>MACD: cắt lên +2; cắt xuống -2; thanh dương +1; thanh âm -1</div>
                  <div>RSI: quá bán +1; thiên mạnh +1; quá mua -1; thiên yếu -1</div>
                  <div>KDJ: cắt lên +1; cắt xuống -1</div>
                  <div>Bollinger: vượt dải trên +1; thủng dải dưới -1</div>
                  <div>Khối lượng: bùng +1; cạn -1</div>
                  <div>Hỗ trợ/kháng cự: đóng cửa ≤ hỗ trợ×1.02 → +1; đóng cửa ≥ kháng cự×0.98 → -1</div>
                </div>
              </div>
            </details>

            <Button variant="secondary" size="sm" className="w-full mt-1" onClick={handleAskAI}>
              <Sparkles className="w-3.5 h-3.5 mr-1" /> Hỏi AI phân tích các chỉ báo này
            </Button>

          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
