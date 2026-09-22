import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'

export type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

export interface KlineEvidenceItem {
  text: string
  details?: string
  delta: number
  tag?: string
}

export interface KlineScoreSuggestion {
  action: Action
  action_label: string
  signal: string
  score: number
  evidence: KlineEvidenceItem[]
  tags: string[]
}

export function buildKlineSuggestion(s: KlineSummaryData, holding?: boolean): KlineScoreSuggestion {
  let score = 0
  const items: KlineEvidenceItem[] = []
  const tags: string[] = []

  const fmt = (n?: number | null, digits: number = 2): string => {
    if (n == null || Number.isNaN(n)) return '--'
    return Number(n).toFixed(digits)
  }

  const tf = s.timeframe || '1d'
  const asof = s.asof ? `tính tới ${s.asof}` : ''

  const addItem = (text: string, delta: number = 0, tag?: string, details?: string) => {
    items.push({ text, delta, tag, details })
    score += delta
    if (tag) tags.push(tag)
  }

  // Xu thế đường trung bình.
  // Lưu ý: đối số của includes() là TỪ KHÓA do backend trả về (trường
  // trend/macd_status/... vẫn là tiếng Trung), không phải chữ hiển thị —
  // đừng dịch, dịch là mất tín hiệu. Chữ hiển thị nằm ở text và tag.
  if (s.trend?.includes('多头')) {
    addItem('Các đường trung bình xếp tăng, xu thế thiên mạnh', 2, 'Xu thế tăng', `Khung ${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('空头')) {
    addItem('Các đường trung bình xếp giảm, xu thế thiên yếu', -2, 'Xu thế giảm', `Khung ${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('交织')) {
    addItem('Các đường trung bình đan xen, xu thế chưa rõ', 0, undefined, `Khung ${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  }

  // MACD
  if (s.macd_status?.includes('金叉')) {
    addItem('MACD cắt lên, động lượng ngắn hạn thiên mạnh', 2, 'MACD cắt lên', `Khung ${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_status?.includes('死叉')) {
    addItem('MACD cắt xuống, động lượng ngắn hạn chuyển yếu', -2, 'MACD cắt xuống', `Khung ${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_hist != null) {
    if (s.macd_hist > 0.0) {
      addItem('Thanh MACD dương (động lượng nghiêng mua)', 1, undefined, `Khung ${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    } else if (s.macd_hist < 0.0) {
      addItem('Thanh MACD âm (động lượng nghiêng bán)', -1, undefined, `Khung ${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    }
  }

  // RSI
  if (s.rsi_status?.includes('超卖')) {
    addItem('RSI quá bán, có thể bật lại', 1, 'RSI quá bán', `Khung ${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (ngưỡng <20)`)
  } else if (s.rsi_status?.includes('偏强')) {
    addItem('RSI mạnh, bên mua chiếm ưu thế', 1, 'RSI mạnh', `Khung ${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (ngưỡng 70-80)`)
  } else if (s.rsi_status?.includes('超买')) {
    addItem('RSI quá mua, coi chừng nhịp điều chỉnh', -1, 'RSI quá mua', `Khung ${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (ngưỡng >80)`)
  } else if (s.rsi_status?.includes('偏弱')) {
    addItem('RSI yếu, ngắn hạn chịu áp lực', -1, 'RSI yếu', `Khung ${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (ngưỡng <30)`)
  } else if (s.rsi_status?.includes('中性')) {
    addItem('RSI trung tính', 0, undefined, `Khung ${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}`)
  }

  // KDJ
  if (s.kdj_status?.includes('金叉')) {
    addItem('KDJ cắt lên, ngắn hạn chuyển mạnh', 1, 'KDJ cắt lên', `Khung ${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }
  if (s.kdj_status?.includes('死叉')) {
    addItem('KDJ cắt xuống, ngắn hạn chuyển yếu', -1, 'KDJ cắt xuống', `Khung ${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }

  // BOLL
  if (s.boll_status?.includes('突破上轨')) {
    addItem('Vượt dải Bollinger trên, xu thế mạnh', 1, 'Vượt dải trên', `Khung ${tf} ${asof} · đóng cửa: ${fmt(s.last_close)} · dải trên: ${fmt(s.boll_upper)}`)
  } else if (s.boll_status?.includes('跌破下轨')) {
    addItem('Thủng dải Bollinger dưới, diễn biến thiên yếu', -1, 'Thủng dải dưới', `Khung ${tf} ${asof} · đóng cửa: ${fmt(s.last_close)} · dải dưới: ${fmt(s.boll_lower)}`)
  }

  // Volume
  if (s.volume_trend?.includes('放量')) {
    addItem('Khối lượng bùng lên đồng thuận, dòng tiền tham gia nhiều hơn', 1, 'Bùng khối lượng', `Khung ${tf} ${asof} · tỷ lệ khối lượng: ${fmt(s.volume_ratio, 1)}x`)
  } else if (s.volume_trend?.includes('缩量')) {
    addItem('Khối lượng cạn, thiếu động lượng', -1, 'Cạn khối lượng', `Khung ${tf} ${asof} · tỷ lệ khối lượng: ${fmt(s.volume_ratio, 1)}x`)
  }

  // Support / Resistance proximity
  if (s.last_close != null && s.support != null && s.support > 0) {
    if (s.last_close <= s.support * 1.02) {
      const dist = (s.last_close - s.support) / s.support * 100
      addItem('Giá sát vùng hỗ trợ, xác suất chặn đà giảm rồi bật lại tăng lên', 1, 'Sát hỗ trợ', `Khung ${tf} ${asof} · đóng cửa: ${fmt(s.last_close)} · hỗ trợ: ${fmt(s.support)} · khoảng cách: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (ngưỡng <=+2%)`)
    }
  }
  if (s.last_close != null && s.resistance != null && s.resistance > 0) {
    if (s.last_close >= s.resistance * 0.98) {
      const dist = (s.last_close - s.resistance) / s.resistance * 100
      addItem('Giá sát vùng kháng cự, dư địa đi lên bị chặn', -1, 'Sát kháng cự', `Khung ${tf} ${asof} · đóng cửa: ${fmt(s.last_close)} · kháng cự: ${fmt(s.resistance)} · khoảng cách: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (ngưỡng >=-2%)`)
    }
  }

  const holdingFlag = holding === true
  let action: Action
  if (holdingFlag) {
    if (score >= 3) action = 'add'
    else if (score >= 1) action = 'hold'
    else if (score <= -3) action = 'sell'
    else if (score <= -1) action = 'reduce'
    else action = 'watch'
  } else {
    if (score >= 3) action = 'buy'
    else if (score <= -2) action = 'avoid'
    else action = 'watch'
  }

  const uniqTags = Array.from(new Set(tags))
  const signal = uniqTags.length > 0 ? uniqTags.join(' / ') : 'Kỹ thuật trung tính'

  const actionLabel = (a: Action): string => {
    switch (a) {
      case 'buy': return 'Mua vào'
      case 'add': return 'Gia tăng tỷ trọng'
      case 'reduce': return 'Hạ tỷ trọng'
      case 'sell': return 'Bán ra'
      case 'hold': return 'Nắm giữ'
      case 'watch': return 'Quan sát'
      case 'avoid': return 'Tránh ra'
      default: return 'Quan sát'
    }
  }

  return {
    action,
    action_label: actionLabel(action),
    signal,
    score,
    evidence: items,
    tags: uniqTags,
  }
}
