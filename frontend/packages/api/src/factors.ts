import { fetchAPI } from './client'

export interface FactorWeight {
  factor_code: string
  market: string
  weight: number
  is_pinned: boolean
  auto_calibrate: boolean
  last_ic: number | null
  last_ir: number | null
  last_sample_size: number | null
  last_calibrated_at: string | null
  reason: string
  updated_at: string | null
}

export interface FactorWeightUpdatePayload {
  weight?: number
  is_pinned?: boolean
  auto_calibrate?: boolean
}

export const factorsApi = {
  /** Danh sách trọng số nhân tố (mỗi nhân tố tách theo thị trường, kèm kết quả chuẩn định IC/IR gần nhất). */
  list: () => fetchAPI<{ items: FactorWeight[] }>('/factors/weights'),

  /** Cập nhật trọng số của một nhân tố (trọng số tay / khóa / công tắc tự chuẩn định). */
  update: (factorCode: string, market: string, patch: FactorWeightUpdatePayload) =>
    fetchAPI<FactorWeight>(`/factors/weights/${factorCode}/${market}`, {
      method: 'POST',
      body: JSON.stringify(patch),
    }),
}
