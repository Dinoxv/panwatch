import { fetchAPI } from './client'

export interface SelfCheckItem {
  category: 'datasource' | 'ai' | 'notify'
  key: string
  name: string
  status: 'ok' | 'slow' | 'fail'
  latency_ms: number
  error: string | null
  /** Gợi ý cách sửa bằng tiếng Việt (chỉ khác rỗng khi fail). */
  hint: string
  /** Ví dụ thông báo "chỉ kiểm cấu hình chứ chưa gửi thật". */
  note: string | null
  /** Nhóm cấp hai (hạng mục AI = tên nhà cung cấp); các hạng mục khác là null. */
  group: string | null
}

export interface SelfCheckResult {
  items: SelfCheckItem[]
  summary: {
    total: number
    ok: number
    slow: number
    fail: number
  }
  notify_send: boolean
}

export const healthApi = {
  /** Tự kiểm hệ thống (nguồn dữ liệu/AI/thông báo còn thông không). notifySend=true sẽ gửi thông báo thử thật. */
  selfcheck: (notifySend = false) =>
    fetchAPI<SelfCheckResult>('/health/selfcheck?notify_send=' + notifySend, { timeoutMs: 60000 }),

  /** Chỉ lấy danh sách các mục tự kiểm được (không dò, trả về tức thì), để dựng trước rồi kiểm từng mục sau. */
  selfcheckList: () =>
    fetchAPI<{ items: Array<{ category: string; key: string; name: string; group: string | null }> }>(
      '/health/selfcheck?list=1',
    ),

  /** Chỉ dò vài mục theo key chỉ định (dùng cho tự kiểm từng mục/song song ít). notifySend chỉ ảnh hưởng hạng mục notify. */
  selfcheckKeys: (keys: string[], notifySend = false) =>
    fetchAPI<SelfCheckResult>(
      '/health/selfcheck?keys=' +
        encodeURIComponent(keys.join(',')) +
        '&notify_send=' +
        notifySend,
      { timeoutMs: 30000 },
    ),
}
