import { fetchAPI } from './client'

export interface ResetToSeedDeletedItem {
  id: number
  type: string
  provider: string
  name: string
}

export interface ResetToSeedSeededItem {
  name: string
  type: string
  provider: string
}

export interface ResetToSeedResult {
  deleted: ResetToSeedDeletedItem[]
  seeded_missing: ResetToSeedSeededItem[]
}

/** "Khôi phục mặc định" của nguồn dữ liệu: xóa nguồn mồ côi + bù nguồn mặc định còn thiếu, đặt lại mã kiểm thử dựng sẵn, giữ cấu hình/chứng thực của người dùng. */
export const resetDataSourcesToSeed = () =>
  fetchAPI<ResetToSeedResult>('/datasources/reset-to-seed', { method: 'POST' })
