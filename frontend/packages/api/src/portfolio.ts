import { fetchAPI } from './client'

export interface PortfolioDiagnostics {
  position_count: number
  total_market_value: number
  hhi: number
  max_weight: number
  by_market: Record<string, number>
  by_strategy: Record<string, number>
  total_unrealized_pnl: number
  alerts: string[]
}

export interface BenchmarkCurvePoint {
  date: string
  portfolio: number
  benchmark: number
}

export interface PortfolioBenchmark {
  empty?: boolean
  reason?: string
  portfolio_return?: number
  benchmark_return?: number
  excess_return?: number
  information_ratio?: number
  relative_drawdown?: number
  days?: number
  benchmark_code?: string
  benchmark_label?: string
  curve?: BenchmarkCurvePoint[]
}

export const portfolioApi = {
  /** Soi danh mục thật (mức tập trung/phân bố/cảnh báo rủi ro). */
  diagnostics: () => fetchAPI<PortfolioDiagnostics>('/portfolio/diagnostics'),

  /** Danh mục vs tham chiếu (vượt trội/tỷ lệ thông tin/sụt giảm tương đối + đường đã chuẩn hóa). */
  benchmark: (params?: { days?: number; benchmark?: string }) =>
    fetchAPI<PortfolioBenchmark>(
      `/portfolio/benchmark?days=${params?.days ?? 60}&benchmark=${encodeURIComponent(params?.benchmark ?? '000300')}`,
      { timeoutMs: 60000 },
    ),

  /** Mức đóng góp của từng mã vào lợi nhuận danh mục (ai kéo lùi/ai đóng góp). */
  attribution: (days = 60) =>
    fetchAPI<{ items: AttributionItem[] }>(`/portfolio/attribution?days=${days}`, { timeoutMs: 60000 }),

  /** Soi sức khỏe danh mục bằng AI (kết luận dạng văn + khuyến nghị cơ cấu lại). */
  aiReview: () => fetchAPI<PortfolioAiReview>('/portfolio/ai-review', { method: 'POST', timeoutMs: 60000 }),
}

export interface AttributionItem {
  symbol: string
  name: string
  market: string
  return_pct: number
  weight_pct: number
  contribution_pct: number
}

export interface PortfolioAiReview {
  empty?: boolean
  reason?: string
  content?: string
  top?: AttributionItem[]
  worst?: AttributionItem[]
}
