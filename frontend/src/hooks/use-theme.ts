import { useState, useEffect } from 'react'

/** Chế độ giao diện người dùng chọn (system = theo hệ thống). */
export type ThemeMode = 'light' | 'dark' | 'system'
/** Giao diện thực sự có hiệu lực (kết quả sau khi giải nghĩa system). */
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'panwatch-theme'

function readMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readMode)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  // Theo hệ thống: nghe thay đổi giao diện của OS, phản ánh tức thì
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Giao diện hiệu lực: light/dark tường minh thì dùng thẳng, system thì bám theo hệ thống hiện tại
  const theme: Theme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(theme)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [theme, mode])

  // Tương thích lối gọi cũ: đảo qua lại sáng/tối (sẽ chốt chế độ thành light/dark tường minh)
  const toggleTheme = () => setMode(theme === 'dark' ? 'light' : 'dark')

  return { theme, mode, setMode, toggleTheme }
}
