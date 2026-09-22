export const TERMINAL_PROGRESS_STATUSES = ['success', 'failed', 'stale'] as const

export type TerminalProgressStatus = typeof TERMINAL_PROGRESS_STATUSES[number]

export function isTerminalProgressStatus(status: string | null | undefined): status is TerminalProgressStatus {
  return TERMINAL_PROGRESS_STATUSES.includes(status as TerminalProgressStatus)
}

/**
 * SSE đóng chỉ có nghĩa là kết nối này kết thúc, không có nghĩa tác vụ kết thúc.
 * not_found, running, timeout và trạng thái rỗng đều phải giao lại cho polling chạy tiếp.
 */
export function shouldContinueProgressWatch(
  status: string | null | undefined,
  event: 'progress' | 'done' = 'progress',
): boolean {
  void event
  return !isTerminalProgressStatus(status)
}
