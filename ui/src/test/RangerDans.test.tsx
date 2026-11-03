import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Project } from '../api'
import { LangProvider } from '../components/LangProvider'
import { RangerDans } from '../components/RangerDans'

const P = (id: string | null, name: string, kind: Project['kind'] = 'regular'): Project =>
  ({ id, project: name, name, kind, owner: 'moi', count: 1 })
const PROJETS = [P('inbox', 'Non classé', 'inbox'), P('a', 'Réunions AMI'), P('b', 'Produit'), P(null, 'Ancien sans identifiant')]

function monter(props: Partial<React.ComponentProps<typeof RangerDans>> = {}) {
  const surChoisir = vi.fn()
  const surCreer = vi.fn(async (nom: string) => ({ id: 'nouveau', project: nom, name: nom, kind: 'regular', count: 0 }) as Project)
  render(<LangProvider><RangerDans projets={PROJETS} destination="a" surChoisir={surChoisir} surCreer={surCreer} {...props} /></LangProvider>)
  return { surChoisir, surCreer }
}

describe('« Ranger dans » (US2)', () => {
  it('est un combobox qui affiche la destination courante et son aide', () => {
    monter()
    const champ = screen.getByRole('combobox', { name: /ranger dans|save to/i })
    expect(champ).toHaveValue('Réunions AMI')
    expect(screen.getByText(/ne change pas ce que vous consultez/i)).toBeInTheDocument()
    expect(champ).toHaveAttribute('aria-describedby')
  })

  it('propose les projets du compte, jamais ceux en lecture seule ni sans identifiant', async () => {
    monter()
    await userEvent.click(screen.getByRole('combobox'))
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(expect.arrayContaining([expect.stringMatching(/non classé/i), 'Réunions AMI', 'Produit']))
    expect(options.join('|')).not.toMatch(/Ancien sans identifiant/)
  })

  it('filtre en tapant, sans tenir compte des accents', async () => {
    monter()
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'reunions')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Réunions AMI', expect.stringMatching(/nouveau projet/i)])
  })

  it('choisir une option appelle surChoisir et ferme la liste', async () => {
    const { surChoisir } = monter()
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'Produit' }))
    expect(surChoisir).toHaveBeenCalledWith('b')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('se pilote au clavier : flèches, Entrée, Échap', async () => {
    const { surChoisir } = monter()
    const champ = screen.getByRole('combobox')
    await userEvent.click(champ)
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(surChoisir).toHaveBeenCalledTimes(1)
    await userEvent.click(champ)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('crée un projet depuis une saisie qui ne correspond à aucun, puis le choisit', async () => {
    const { surChoisir, surCreer } = monter()
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'Séminaire')
    await userEvent.click(screen.getByRole('option', { name: /nouveau projet.*séminaire/i }))
    expect(surCreer).toHaveBeenCalledWith('Séminaire')
    await vi.waitFor(() => expect(surChoisir).toHaveBeenCalledWith('nouveau'))
  })

  it('n\'offre pas de créer un projet qui existe déjà (même nom, accents ignorés)', async () => {
    monter()
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'Reunions AMI')
    expect(screen.queryByRole('option', { name: /nouveau projet/i })).not.toBeInTheDocument()
  })

  it('annonce l\'absence de résultat et garde la création possible', async () => {
    monter()
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'zzz')
    expect(screen.getByText(/aucun projet ne correspond/i)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /nouveau projet.*zzz/i })).toBeInTheDocument()
  })

  it('rétablit le nom de la destination quand on quitte sans choisir', async () => {
    const { surChoisir } = monter()
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'xx')
    await userEvent.tab()
    expect(champ).toHaveValue('Réunions AMI')
    expect(surChoisir).not.toHaveBeenCalled()
  })

  it('affiche le nom par défaut quand le compte n\'a pas encore de « Non classé »', () => {
    monter({ destination: '', nomParDefaut: 'Non classé' })
    expect(screen.getByRole('combobox', { name: /ranger dans|save to/i })).toHaveValue('Non classé')
  })

  it('signale « proposé : le dernier projet utilisé » quand la destination est mémorisée', () => {
    monter({ proposee: true })
    expect(screen.getByText(/proposé : le dernier projet utilisé/i)).toBeInTheDocument()
  })
})
