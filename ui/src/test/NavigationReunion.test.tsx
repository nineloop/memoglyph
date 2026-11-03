import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { NavigationReunion } from '../components/NavigationReunion'

describe('les flèches précédente et suivante', () => {
  it('mènent à la réunion voisine ; absentes ou inactives aux extrémités, sans erreur', async () => {
    const aller = vi.fn()
    const { rerender } = render(<LangProvider><NavigationReunion precedente={{ cle: 'p', titre: 'Avant' }}
                                                                 suivante={{ cle: 's', titre: 'Après' }} surAller={aller} /></LangProvider>)
    await userEvent.click(screen.getByRole('button', { name: /réunion précédente|previous meeting/i }))
    expect(aller).toHaveBeenLastCalledWith('p')
    await userEvent.click(screen.getByRole('button', { name: /réunion suivante|next meeting/i }))
    expect(aller).toHaveBeenLastCalledWith('s')

    rerender(<LangProvider><NavigationReunion suivante={{ cle: 's', titre: 'Après' }} surAller={aller} /></LangProvider>)
    expect(screen.getByRole('button', { name: /réunion précédente|previous meeting/i })).toBeDisabled()
    rerender(<LangProvider><NavigationReunion surAller={aller} /></LangProvider>)
    expect(screen.getByRole('button', { name: /réunion suivante|next meeting/i })).toBeDisabled()
  })
})
