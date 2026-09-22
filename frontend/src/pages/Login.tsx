import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Lock, Eye, EyeOff, User } from 'lucide-react'
import { authApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export default function LoginPage() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [isSetup, setIsSetup] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    // Kiểm tra trạng thái xác thực
    authApi.status()
      .then(data => {
        setIsSetup(!data.initialized)
        setChecking(false)
      })
      .catch(() => setChecking(false))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return

    if (isSetup) {
      if (password !== confirmPassword) {
        toast('Hai lần nhập mật khẩu không khớp', 'error')
        return
      }
      if (password.length < 6) {
        toast('Mật khẩu phải dài ít nhất 6 ký tự', 'error')
        return
      }
    }

    setLoading(true)
    try {
      const data = isSetup
        ? await authApi.setup({ username, password })
        : await authApi.login({ username, password })

      // Lưu token
      localStorage.setItem('token', data.token)
      localStorage.setItem('token_expires', data.expires_at)

      toast(isSetup ? 'Đặt mật khẩu thành công' : 'Đăng nhập thành công', 'success')
      navigate('/')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Thao tác thất bại', 'error')
    } finally {
      setLoading(false)
    }
  }

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mb-4">
            <TrendingUp className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">PanWatch</h1>
          <p className="text-sm text-muted-foreground mt-1">PanWatch</p>
        </div>

        {/* Form */}
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-6">
            <Lock className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-semibold">
              {isSetup ? 'Đặt mật khẩu truy cập' : 'Đăng nhập'}
            </h2>
          </div>

          {isSetup && (
            <p className="text-sm text-muted-foreground mb-4">
              Lần đầu dùng, xin đặt mật khẩu truy cập để bảo vệ dữ liệu của bạn
            </p>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label>Tên đăng nhập</Label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="Xin nhập tên đăng nhập"
                  className="pl-10"
                  autoFocus
                />
              </div>
            </div>

            <div>
              <Label>{isSetup ? 'Đặt mật khẩu' : 'Mật khẩu'}</Label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={isSetup ? 'Ít nhất 6 ký tự' : 'Xin nhập mật khẩu'}
                  className="pl-10 pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>

            {isSetup && (
              <div>
                <Label>Xác nhận mật khẩu</Label>
                <Input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  placeholder="Nhập lại mật khẩu"
                />
              </div>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : isSetup ? (
                'Đặt mật khẩu rồi vào'
              ) : (
                'Đăng nhập'
              )}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-muted-foreground mt-6">
          Trợ lý theo dõi cổ phiếu chạy bằng AI
        </p>
      </div>
    </div>
  )
}
