import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'

describe('AssistantWelcome', () => {
  it('starts a focused research question from a suggested entry point', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(<AssistantWelcome onSubmit={onSubmit} />)

    expect(screen.getByRole('heading', { name: 'Hôm nay muốn nghiên cứu gì?' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Soi danh mục của tôi' }))

    expect(onSubmit).toHaveBeenCalledWith('Soi rủi ro và điểm cần lưu ý trong danh mục của tôi')
  })
})
