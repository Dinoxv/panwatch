import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AgentPermissionsPanel } from '@/components/assistant/AgentPermissionsPanel'

describe('agent tool permission settings', () => {
  it('renders safe defaults, sends a tool override and never offers destructive allow', () => {
    const onChange = vi.fn()
    render(
      <AgentPermissionsPanel
        permissions={{
          defaults: [
            { risk: 'read', mode: 'allow' },
            { risk: 'write', mode: 'ask' },
            { risk: 'external', mode: 'ask' },
            { risk: 'destructive', mode: 'deny' },
          ],
          tools: [
            { name: 'create_alert', title: 'Tạo cảnh báo', risk: 'write', mode: 'ask', confirmation_required: false },
            { name: 'delete_alert', title: 'Xóa cảnh báo', risk: 'destructive', mode: 'deny', confirmation_required: false },
          ],
        }}
        onChange={onChange}
      />,
    )

    expect((screen.getByLabelText('Quyền mặc định Đọc') as HTMLSelectElement).value).toBe('allow')
    expect((screen.getByLabelText('Quyền mặc định Sửa') as HTMLSelectElement).value).toBe('ask')
    expect((screen.getByLabelText('Quyền mặc định Thao tác ra ngoài') as HTMLSelectElement).value).toBe('ask')
    const destructiveDefault = screen.getByLabelText('Quyền mặc định Thao tác phá hủy') as HTMLSelectElement
    expect(destructiveDefault.value).toBe('deny')
    expect([...destructiveDefault.options].some((option) => option.value === 'allow')).toBe(false)

    fireEvent.change(screen.getByLabelText('Quyền mặc định Sửa'), { target: { value: 'allow' } })
    expect(onChange).toHaveBeenCalledWith({
      selector_kind: 'risk',
      selector_value: 'write',
      mode: 'allow',
      risk: 'write',
    })

    fireEvent.change(screen.getByLabelText('Tạo cảnh báo'), { target: { value: 'allow' } })
    expect(onChange).toHaveBeenCalledWith({
      selector_kind: 'tool',
      selector_value: 'create_alert',
      mode: 'allow',
      risk: 'write',
    })

    const destructive = screen.getByLabelText('Xóa cảnh báo') as HTMLSelectElement
    expect([...destructive.options].some((option) => option.value === 'allow')).toBe(false)
  })
})
