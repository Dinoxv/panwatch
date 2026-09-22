import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApprovalCard } from '@/components/assistant/ApprovalCard'

describe('ApprovalCard', () => {
  it('allows exactly one explicit decision and shows the operation summary', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: 'Tạo cảnh báo',
          risk: 'write',
          summary: 'Tạo cảnh báo giá cho 贵州茅台',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'pending',
        }}
        onDecision={onDecision}
      />,
    )

    expect(screen.getByText('Tạo cảnh báo giá cho 贵州茅台')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Cho phép lần này' }))

    expect(onDecision).toHaveBeenCalledTimes(1)
    expect(onDecision).toHaveBeenCalledWith('approved')
    expect((screen.getByRole('button', { name: 'Từ chối' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps a decided card visible with its execution status', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: 'Tạo cảnh báo',
          risk: 'write',
          summary: 'Tạo cảnh báo giá cho 贵州茅台',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'approved',
        }}
        onDecision={vi.fn()}
      />,
    )

    expect(screen.getByText('Đã cho phép, đã chạy')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cho phép lần này' })).toBeNull()
  })

  it('shows a rejected card as a terminal decision without actions', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-2',
          tool_title: 'Tạo cảnh báo',
          risk: 'write',
          summary: 'Tạo cảnh báo giá cho 贵州茅台',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'rejected',
        }}
        onDecision={vi.fn()}
      />,
    )

    const status = screen.getByText('Đã từ chối, sẽ không chạy')
    expect(status.className).toContain('text-destructive')
    expect(screen.queryByRole('button', { name: 'Từ chối' })).toBeNull()
  })
})
