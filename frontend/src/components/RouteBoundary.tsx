import { Component, type ErrorInfo, type ReactNode } from 'react'

interface RouteErrorBoundaryProps {
  children: ReactNode
}

interface RouteErrorBoundaryState {
  error: Error | null
}

export function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[320px] items-center justify-center rounded-2xl border border-border/40 bg-card/30">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
        Đang tải trang…
      </div>
    </div>
  )
}

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Tải module trang thất bại: ', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-2xl border border-destructive/20 bg-card/30 px-6 text-center">
          <p className="text-sm font-medium text-foreground">Tải trang thất bại</p>
          <p className="max-w-md text-xs text-muted-foreground">Hãy thử lại; nếu vẫn lỗi thì có thể trình duyệt đang giữ bản trang cũ trong bộ đệm.</p>
          <button
            type="button"
            className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
            onClick={() => window.location.reload()}
          >
            Tải lại
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
