// Hạ tầng máy khách SSE: dựng trên fetch + ReadableStream (EventSource gốc không mang được header Authorization)
// - readSSE: một lần kết nối, luồng kết thúc thì resolve; kết nối hỏng thì reject thẳng (bên gọi dựa vào đó để hạ xuống hỏi vòng/không luồng)
// - subscribeSSE: đăng ký có tự kết nối lại (mang Last-Event-ID để đẩy tiếp), dùng cho các luồng GET kiểu tiến độ/nhật ký
import { getToken } from './client'

export interface SSEEvent {
  /** Số thứ tự sự kiện (server tự tăng, dùng khi đứt rồi nối lại) */
  id: number
  event: string
  /** Kết quả sau khi JSON.parse dòng data; phân tích hỏng thì là chuỗi gốc */
  data: any
}

export interface ReadSSEOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  /** Mang theo khi nối lại sau đứt, server đẩy tiếp từ sau mốc đó */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
}

/** Đọc một khối văn bản SSE wire (không gồm dòng trống phân cách ở cuối) */
function parseEventBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  if (lines.every((l) => !l || l.startsWith(':'))) return null // Chú thích nhịp tim
  let id = 0
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('id: ')) id = parseInt(line.slice(4), 10) || 0
    else if (line.startsWith('event: ')) event = line.slice(7)
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    else if (line === 'data:') dataLines.push('')
  }
  const raw = dataLines.join('\n')
  let data: any = raw
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      /* Giữ nguyên chuỗi gốc */
    }
  }
  return { id, event, data }
}

/**
 * Mở một kết nối SSE và tiêu thụ tới khi luồng kết thúc.
 * Trả về số thứ tự sự kiện cuối cùng nhận được (để bên gọi nối lại đẩy tiếp).
 * Kết nối hỏng (HTTP không 2xx / sai content-type / lỗi mạng) thì ném lỗi.
 */
export async function readSSE(path: string, options: ReadSSEOptions): Promise<{ lastEventId: number }> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.lastEventId && options.lastEventId > 0) {
    headers['Last-Event-ID'] = String(options.lastEventId)
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })
  if (!res.ok) throw new Error(`SSE HTTP ${res.status}`)
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) throw new Error(`Phản hồi không phải SSE: ${contentType}`)
  if (!res.body) throw new Error('Phản hồi SSE không có body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventId = options.lastEventId || 0

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // Các sự kiện cách nhau bằng dòng trống
    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      const ev = parseEventBlock(block)
      if (ev) {
        if (ev.id > 0) lastEventId = ev.id
        options.onEvent(ev)
      }
    }
  }
  return { lastEventId }
}

export interface SubscribeSSEOptions {
  /** Mốc đẩy tiếp cho lần kết nối đầu (ví dụ id nhật ký lớn nhất đã biết) */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
  /** Gọi trước mỗi lần kết nối (lại) thành công, dùng cho trạng thái giao diện */
  onRetry?: (attempt: number) => void
  /** Gọi khi đã cạn số lần thử lại (bên gọi hạ xuống hỏi vòng) */
  onFailed?: (err: unknown) => void
  /** Gọi khi server đóng luồng bình thường (ví dụ luồng hết giờ; bên gọi đăng ký lại hoặc hạ cấp) */
  onClosed?: () => void
  maxRetries?: number
}

/**
 * Đăng ký SSE có tự kết nối lại (GET). Đứt thì lùi theo cấp số nhân rồi nối lại,
 * mang Last-Event-ID để đẩy tiếp.
 * Trả về hàm hủy; dừng khi server đóng luồng bình thường (nhận sự kiện done rồi
 * bên gọi chủ động close) hoặc khi cạn số lần thử lại.
 */
export function subscribeSSE(path: string, options: SubscribeSSEOptions): () => void {
  const controller = new AbortController()
  let closed = false
  let lastEventId = options.lastEventId || 0
  const maxRetries = options.maxRetries ?? 5

  const loop = async () => {
    let attempt = 0
    while (!closed) {
      try {
        const { lastEventId: newId } = await readSSE(path, {
          signal: controller.signal,
          lastEventId,
          onEvent: (ev) => {
            if (ev.id > 0) lastEventId = ev.id
            attempt = 0 // Nhận được dữ liệu là đặt lại bộ đếm thử lại
            options.onEvent(ev)
          },
        })
        lastEventId = newId
        // Server đóng luồng bình thường (ví dụ done do hết giờ): để bên gọi quyết định có đăng ký lại không, ở đây thoát
        if (!closed) options.onClosed?.()
        return
      } catch (err) {
        if (closed || controller.signal.aborted) return
        attempt += 1
        if (attempt > maxRetries) {
          options.onFailed?.(err)
          return
        }
        options.onRetry?.(attempt)
        // Lùi theo cấp số nhân: 1s/2s/4s/8s/8s...
        const delay = Math.min(1000 * 2 ** (attempt - 1), 8000)
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
  void loop()

  return () => {
    closed = true
    controller.abort()
  }
}
