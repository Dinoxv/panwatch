import { useState, useEffect } from 'react'
export { cn } from '@panwatch/base-ui'

/**
 * useState có lưu bền xuống localStorage
 * @param key tên khóa trong localStorage
 * @param defaultValue giá trị mặc định
 */
export function useLocalStorage<T>(key: string, defaultValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key)
      if (saved !== null) {
        return JSON.parse(saved)
      }
    } catch {
      // ignore
    }
    return defaultValue
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [key, value])

  return [value, setValue]
}

// ==================== Tiện ích định dạng thời gian ====================

/**
 * Định dạng thời gian ISO thành giờ địa phương (chỉ phần giờ)
 * @param isoTime chuỗi thời gian định dạng ISO
 * @returns ví dụ "15:30"
 */
export function formatTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * Định dạng thời gian ISO thành ngày giờ địa phương
 * @param isoTime chuỗi thời gian định dạng ISO
 * @returns ví dụ "01/26 15:30"
 */
export function formatDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * Định dạng thời gian ISO thành ngày giờ địa phương đầy đủ
 * @param isoTime chuỗi thời gian định dạng ISO
 * @returns ví dụ "2024-01-26 15:30:00"
 */
export function formatFullDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}
