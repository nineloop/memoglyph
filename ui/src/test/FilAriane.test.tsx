import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FilAriane } from '../components/FilAriane'
import { LangProvider } from '../components/LangProvider'

describe('le fil d\'Ariane (US1)', () => {
  it('Bibliothèque > projet > réunion : chaque niveau ramène à son écran en UN clic', async () => {
    const biblio = vi.fn()
    const projet = vi.fn()
    render(<LangProvider><FilAriane projet={{ id: 'a', nom: 'Réunions AMI' }} reunion="IS1009a"
                                    surBibliotheque={biblio} surProjet={projet} /></LangProvider>)
    const nav = screen.getByRole('navigation', { name: /fil d.ariane|breadcrumb/i })
    await userEvent.click(within(nav).getByRole('link', { name: /bibliothèque|library/i }))
    expect(biblio).toHaveBeenCalledTimes(1)
    await userEvent.click(within(nav).getByRole('link', { name: /réunions ami/i }))
    expect(projet).toHaveBeenCalledTimes(1)
    // Le dernier niveau est la page courante : un texte, pas un lien.
    expect(within(nav).queryByRole('link', { name: /is1009a/i })).not.toBeInTheDocument()
    expect(within(nav).getByText('IS1009a')).toHaveAttribute('aria-current', 'page')
  })

  it('dans la liste d\'un projet : le projet est la page courante', () => {
    render(<LangProvider><FilAriane projet={{ id: 'a', nom: 'Réunions AMI' }} surBibliotheque={() => {}} surProjet={() => {}} /></LangProvider>)
    expect(screen.getByText('Réunions AMI')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /bibliothèque|library/i })).toBeInTheDocument()
  })

  it('le séparateur suit son élément (à la coupure de ligne il reste en fin de ligne, jamais en tête)', () => {
    render(<LangProvider><FilAriane projet={{ id: 'a', nom: 'Réunions AMI' }} reunion="IS1009a" surBibliotheque={() => {}} surProjet={() => {}} /></LangProvider>)
    const items = [...document.querySelectorAll('.fil-ariane li')]
    expect(items).toHaveLength(3)
    // Chaque élément sauf le dernier se termine par son séparateur ; aucun ne commence par un séparateur.
    for (const li of items.slice(0, -1)) expect(li.lastElementChild).toHaveClass('fil-sep')
    for (const li of items) expect(li.firstElementChild).not.toHaveClass('fil-sep')
    expect(items[2].querySelector('.fil-sep')).toBeNull()
  })

  it('« Tous mes projets » : seule la Bibliothèque, page courante', () => {
    render(<LangProvider><FilAriane surBibliotheque={() => {}} surProjet={() => {}} /></LangProvider>)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(/bibliothèque|library/i)).toHaveAttribute('aria-current', 'page')
  })

  it('les liens portent une adresse (clic du milieu, copie)', () => {
    render(<LangProvider><FilAriane projet={{ id: 'a', nom: 'Réunions AMI' }} reunion="X" surBibliotheque={() => {}} surProjet={() => {}} /></LangProvider>)
    expect(screen.getByRole('link', { name: /réunions ami/i })).toHaveAttribute('href', expect.stringContaining('projet=a'))
  })
})
