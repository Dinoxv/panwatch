import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Moon, Sun, Monitor, Check, LogOut, User, Stethoscope, type LucideIcon } from 'lucide-react'
import { isAuthenticated, logout } from '@panwatch/api'
import type { ThemeMode } from '@/hooks/use-theme'
import { useAvatar } from '@/hooks/use-avatar'

export interface AccountNavItem {
  to: string
  icon: LucideIcon
  label: string
}

const THEME_OPTIONS: { value: ThemeMode; icon: LucideIcon; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Sáng' },
  { value: 'dark', icon: Moon, label: 'Tối' },
  { value: 'system', icon: Monitor, label: 'Theo hệ thống' },
]

interface AccountMenuProps {
  /** Các mục điều hướng vốn gập trong "Thêm" (Agent / Lịch sử / Nguồn dữ liệu / Cài đặt). */
  navItems: AccountNavItem[]
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  /** Mở hộp thoại «Tự kiểm hệ thống» (trạng thái do App tầng trên giữ, tránh dựng trùng hai bản desktop/di động). */
  onOpenSelfCheck: () => void
  /** Cỡ ảnh đại diện: desktop md, di động sm. */
  size?: 'sm' | 'md'
}

/**
 * Vùng ảnh đại diện góc trên phải + menu xổ xuống (tham khảo beecount-cloud):
 * gom điều hướng "Thêm", màu giao diện (sáng/tối/theo hệ thống) và đăng xuất vào
 * menu xổ xuống của ảnh đại diện (Xem nhật ký / GitHub vẫn để ngoài).
 */
export default function AccountMenu({
  navItems,
  mode,
  onSetMode,
  onOpenSelfCheck,
  size = 'md',
}: AccountMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const location = useLocation()
  const avatar = useAvatar()
  // Chỉ bật mở theo rê chuột trên thiết bị có hover (PC); màn cảm ứng vẫn dùng chạm
  const [canHover] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(hover: hover)').matches,
  )

  // Bấm ra ngoài thì đóng
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // Đổi route thì đóng
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  const avatarSize = size === 'sm' ? 'w-6 h-6' : 'w-7 h-7'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  return (
    <div
      className="relative"
      ref={ref}
      onMouseEnter={canHover ? () => setOpen(true) : undefined}
      onMouseLeave={canHover ? () => setOpen(false) : undefined}
    >
      <button
        onClick={() => setOpen(v => !v)}
        className={`${avatarSize} rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm ring-1 transition-all ${
          open ? 'ring-primary/50' : 'ring-border/40 hover:ring-primary/40'
        }`}
        title="Tài khoản và cài đặt"
        aria-label="Tài khoản và cài đặt"
      >
        {avatar ? (
          <img src={avatar} alt="Ảnh đại diện" className="w-full h-full object-cover" />
        ) : (
          <User className={`${iconSize} text-white`} />
        )}
      </button>

      {open && (
        // top-full + pt-2: lấy đệm trong suốt nối ảnh đại diện với menu, rê chuột vào không bị đứt
        <div className="absolute right-0 top-full pt-2 z-50">
          <div className="w-48 rounded-xl border border-border/60 bg-card/95 backdrop-blur p-1.5 shadow-xl">
          {/* Điều hướng vốn nằm trong "Thêm" */}
          {navItems.map(({ to, icon: Icon, label }) => {
            const isActive = location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                onClick={() => setOpen(false)}
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  isActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            )
          })}

          <div className="my-1 h-px bg-border/50" />

          {/* Màu giao diện: sáng / tối / theo hệ thống */}
          <div className="px-2.5 pt-0.5 pb-1 text-[11px] text-muted-foreground">Giao diện</div>
          {THEME_OPTIONS.map(({ value, icon: Icon, label }) => {
            const active = mode === value
            return (
              <button
                key={value}
                onClick={() => onSetMode(value)}
                className={`flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  active
                    ? 'text-foreground bg-accent/40'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
                {active && <Check className="w-3.5 h-3.5 ml-auto text-primary" />}
              </button>
            )
          })}

          <div className="my-1 h-px bg-border/50" />
          {/* Tự kiểm hệ thống: mở hộp thoại (soi từng mục nguồn dữ liệu/AI/thông báo còn thông không) */}
          <button
            onClick={() => {
              setOpen(false)
              onOpenSelfCheck()
            }}
            className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <Stethoscope className="w-3.5 h-3.5" />
            Tự kiểm hệ thống
          </button>

          {isAuthenticated() && (
            <>
              <div className="my-1 h-px bg-border/50" />
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" />
                Đăng xuất
              </button>
            </>
          )}
          </div>
        </div>
      )}
    </div>
  )
}
