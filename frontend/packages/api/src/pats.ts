import { fetchAPI } from './client'

/** Mã truy cập cá nhân (PAT) — chứng thực riêng cho điểm cuối MCP */
export interface PatItem {
  id: number
  name: string
  prefix: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string | null
  revoked: boolean
}

/** Phản hồi khi tạo: kèm thêm token dạng chữ thường, chỉ hiện một lần */
export interface PatCreated extends PatItem {
  token: string
}

export interface CreatePatBody {
  name?: string
  scopes?: string[]
  /** Số ngày hết hạn; null = không bao giờ hết hạn */
  expires_in_days?: number | null
}

export const patsApi = {
  list: () => fetchAPI<{ items: PatItem[] }>('/pats'),

  create: (body: CreatePatBody) =>
    fetchAPI<PatCreated>('/pats', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  revoke: (id: number) =>
    fetchAPI<{ ok: boolean; id: number }>(`/pats/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    }),
}
