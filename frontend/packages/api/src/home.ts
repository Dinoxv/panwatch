import { fetchAPI } from './client'

export interface AlertHitToday {
  rule_id: number
  rule_name: string
  symbol: string
  name: string
  market: string
  trigger_time: string
  snapshot: Record<string, unknown>
}

export interface PortfolioTodo {
  type: string // no_alert | alert_expiring
  symbol?: string
  market?: string
  message: string
}

export const homeApi = {
  /** Toàn bộ lượt chạm cảnh báo hôm nay (múi giờ địa phương), gộp qua mọi quy tắc. */
  alertHitsToday: () => fetchAPI<AlertHitToday[]>('/price-alerts/hits/today'),

  /** Việc còn treo ở trạng thái rỗng của trang chủ: vị thế chưa đặt cảnh báo / cảnh báo sắp hết hạn. */
  todos: () => fetchAPI<{ todos: PortfolioTodo[]; count: number }>('/portfolio/todos'),
}
