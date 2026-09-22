import { useState, useEffect } from 'react'
import { fetchAPI } from '@panwatch/api'

const EVENT = 'panwatch:avatar-changed'

// Chỉ là bộ nhớ đệm trong phiên SPA (tránh gọi lại nhiều lần trong một phiên).
// Lưu bền thật sự nằm ở DB backend (cột ui_avatar trong data/panwatch.db), tải lại trang sẽ kéo lại từ backend.
let cache: string | null = null
let inflight: Promise<string> | null = null

function load(): Promise<string> {
  if (cache !== null) return Promise.resolve(cache)
  if (!inflight) {
    inflight = fetchAPI<{ value: string }>('/settings/avatar')
      .then(r => {
        cache = r?.value || ''
        return cache as string
      })
      .catch(() => {
        cache = ''
        return ''
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

/**
 * Lưu ảnh đại diện (truyền chuỗi rỗng = xóa): backend ghi ảnh thành tệp trong
 * data/avatars, DB chỉ giữ tên tệp; phát tin nội bộ để cập nhật tức thì.
 * Lưu ý cache giữ data URL (GET cũng trả về data URL).
 */
export async function saveAvatar(value: string): Promise<void> {
  await fetchAPI('/settings/avatar', { method: 'PUT', body: JSON.stringify({ value }) })
  cache = value
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: value }))
}

/** Ảnh đại diện hiện tại (data URL hoặc địa chỉ ảnh). Nguồn là DB backend; đồng bộ tức thì giữa các thành phần. */
export function useAvatar(): string {
  const [avatar, setAvatar] = useState<string>(cache ?? '')
  useEffect(() => {
    let alive = true
    load().then(v => {
      if (alive) setAvatar(v)
    })
    const onChange = (e: Event) => setAvatar((e as CustomEvent<string>).detail ?? '')
    window.addEventListener(EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(EVENT, onChange)
    }
  }, [])
  return avatar
}

/**
 * Nén tệp ảnh tải lên thành data URL JPEG vuông size×size (cắt theo tâm),
 * khống chế dung lượng (khoảng 10-20KB), tránh base64 quá lớn làm phình DB.
 */
export function fileToAvatarDataUrl(file: File, size = 128): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Đọc tệp thất bại'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('Giải mã ảnh thất bại'))
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          reject(new Error('Không dùng được canvas'))
          return
        }
        const scale = Math.max(size / img.width, size / img.height)
        const w = img.width * scale
        const h = img.height * scale
        ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result as string
    }
    reader.readAsDataURL(file)
  })
}
