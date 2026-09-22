import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Bot, Bell, CheckCircle2, ChevronRight, Sparkles } from 'lucide-react'
import { Dialog, DialogContent } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface OnboardingProps {
  open: boolean
  onComplete: () => void
  hasStocks: boolean
}

type Step = 'welcome' | 'ai' | 'notify' | 'complete'

export function Onboarding({ open, onComplete, hasStocks }: OnboardingProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')

  const handleNext = () => {
    if (step === 'welcome') {
      setStep('ai')
    } else if (step === 'ai') {
      setStep('notify')
    } else if (step === 'notify') {
      setStep('complete')
    } else {
      onComplete()
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  const handleGoToSettings = () => {
    onComplete()
    navigate('/settings')
  }

  const handleGoToPortfolio = () => {
    onComplete()
    navigate('/portfolio')
  }

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onComplete()}>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        {/* Progress Indicator */}
        <div className="flex items-center gap-1.5 px-6 pt-6">
          {(['welcome', 'ai', 'notify', 'complete'] as Step[]).map((s, i) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full transition-colors ${
                i <= ['welcome', 'ai', 'notify', 'complete'].indexOf(step)
                  ? 'bg-primary'
                  : 'bg-accent/50'
              }`}
            />
          ))}
        </div>

        <div className="p-6 pt-4">
          {step === 'welcome' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <TrendingUp className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Chào mừng đến với PanWatch
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                {hasStocks
                  ? 'Danh mục theo dõi của bạn đã sẵn sàng, dùng được rồi'
                  : 'Chúng tôi đã thêm sẵn 5 mã nóng làm ví dụ, bạn xem được bảng giá thời gian thực ngay'
                }
              </p>

              <div className="space-y-3 text-left mb-6">
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Theo dõi bảng giá thời gian thực</p>
                    <p className="text-[12px] text-muted-foreground">Bám sát biến động giá của mã theo dõi, phát hiện nhanh các cú bất thường</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Phân tích thông minh bằng AI</p>
                    <p className="text-[12px] text-muted-foreground">Nhật báo sau phiên, khuyến nghị khi có biến động, phân tích kỹ thuật</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <Bell className="w-4 h-4 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Đẩy thông báo thông minh</p>
                    <p className="text-[12px] text-muted-foreground">Đẩy qua nhiều kênh: Telegram, WeCom…</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button className="flex-1" onClick={handleNext}>
                  Bắt đầu dùng <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
              <button
                onClick={handleSkip}
                className="mt-3 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
              >
                Bỏ qua phần hướng dẫn
              </button>
            </div>
          )}

          {step === 'ai' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <Bot className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Cấu hình phân tích AI
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Nối dịch vụ AI xong sẽ có tính năng phân tích thông minh
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">Tự phân tích nhật báo sau phiên</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">Khuyến nghị AI khi có biến động</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">Phân tích đồ thị kỹ thuật</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Hỗ trợ các nhà cung cấp OpenAI, Zhipu, DeepSeek…
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Để sau
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Đi cấu hình
                </Button>
              </div>
            </div>
          )}

          {step === 'notify' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-amber-500 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Cấu hình kênh thông báo
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Cấu hình xong sẽ nhận được thông báo đẩy thời gian thực
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Nhắc khi có biến động trong phiên</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Đẩy báo cáo phân tích AI</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Cảnh báo chốt lời cắt lỗ</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Hỗ trợ các kênh Telegram, WeCom…
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Để sau
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Đi cấu hình
                </Button>
              </div>
            </div>
          )}

          {step === 'complete' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500 flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Thiết lập xong
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                Bạn sửa cấu hình lúc nào cũng được ở trang «Cài đặt»
              </p>

              <div className="space-y-3">
                <Button className="w-full" onClick={() => onComplete()}>
                  Vào Dashboard
                </Button>
                <Button variant="secondary" className="w-full" onClick={handleGoToPortfolio}>
                  Quản lý danh mục theo dõi
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
