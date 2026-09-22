export type SuggestionAction =
  | 'buy'
  | 'add'
  | 'reduce'
  | 'sell'
  | 'hold'
  | 'watch'
  | 'alert'
  | 'avoid'

export const suggestionActionColors: Record<SuggestionAction, string> = {
  buy: 'bg-rose-500 text-white',
  add: 'bg-rose-400 text-white',
  reduce: 'bg-emerald-500 text-white',
  sell: 'bg-emerald-600 text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  alert: 'bg-blue-500 text-white',
  avoid: 'bg-red-600 text-white',
}

export const suggestionActionLabels: Record<SuggestionAction, string> = {
  buy: 'Mua vào',
  add: 'Gia tăng tỷ trọng',
  reduce: 'Hạ tỷ trọng',
  sell: 'Bán ra',
  hold: 'Nắm giữ',
  watch: 'Quan sát',
  avoid: 'Tránh ra',
  alert: 'Cảnh báo',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'
  if (raw === 'alert') return 'alert'
  // Các mẫu dưới đây khớp với chữ trong khuyến nghị THÔ do mô hình sinh ra
  // (vẫn là tiếng Trung) — đây là từ khóa đối chiếu, không phải chữ hiển thị.
  if (/买入|买|建仓/.test(raw)) return 'buy'
  if (/加仓|增持|补仓/.test(raw)) return 'add'
  if (/减仓|减持/.test(raw)) return 'reduce'
  if (/清仓|卖出|止损|卖/.test(raw)) return 'sell'
  if (/持有|持仓/.test(raw)) return 'hold'
  if (/观望|中性|等待/.test(raw)) return 'watch'
  if (/回避|规避|避免/.test(raw)) return 'avoid'
  return null
}

export function resolveSuggestionAction(action?: string, label?: string): SuggestionAction {
  return normalizeSuggestionAction(action, label) || 'watch'
}

export function resolveSuggestionLabel(action?: string, label?: string, fallback = 'Quan sát'): string {
  const normalized = normalizeSuggestionAction(action, label)
  if (normalized) return suggestionActionLabels[normalized] || fallback
  return String(label || '').trim() || fallback
}

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}
