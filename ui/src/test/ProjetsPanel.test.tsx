import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Partage, Project } from '../api'
import { LangProvider } from '../components/LangProvider'
import { ProjetsPanel, ProjetsTiroir } from '../components/ProjetsPanel'

const P = (id: string, name: string, kind: Project['kind'] = 'regular', count = 2): Project =>
  ({ id, project: name, name, kind, owner: 'moi', count })
const PROJETS = [P('inbox', 'Non classé', 'inbox', 0), P('a', 'Réunions AMI', 'regular', 5), P('b', 'Produit', 'regular', 3), P('c', 'Éléphants', 'regular', 1)]
const RECUS: Partage[] = [
  { id: 'r1', proprietaire: 'zoe', invite: 'moi', type: 'projet', cible: 'z1', cree_le: '', nom: 'Équipe partenaire' },
]

function Cadre(props: { replie?: boolean; surConsulter?: (id: string) => void; surNouveau?: () => void; surBasculer?: (r: boolean) => void; consulte?: string; projets?: Project[]; recus?: Partage[] }) {
  const [replie, setReplie] = useState(props.replie ?? false)
  return (
    <LangProvider>
      <ProjetsPanel projets={props.projets ?? PROJETS} recus={props.recus ?? RECUS} consulte={props.consulte ?? 'a'}
                    replie={replie}
                    surBasculer={(r) => { setReplie(r); props.surBasculer?.(r) }}
                    surConsulter={props.surConsulter ?? (() => {})} surNouveau={props.surNouveau ?? (() => {})} />
    </LangProvider>
  )
}

beforeEach(() => localStorage.clear())

describe('le panneau des projets (US1)', () => {
  it('montre les groupes : Tous mes projets, Non classé, Projets, Partagés avec moi', () => {
    render(<Cadre />)
    const nav = screen.getByRole('navigation', { name: /projets de la bibliothèque|library projects/i })
    expect(within(nav).getByRole('link', { name: /tous mes projets|all my projects/i })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /non classé|unclassified/i })).toBeInTheDocument()
    expect(within(nav).getByRole('heading', { name: /^projets$|^projects$/i })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /réunions ami/i })).toBeInTheDocument()
    expect(within(nav).getByRole('heading', { name: /partagés avec moi|shared with me/i })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /équipe partenaire/i })).toBeInTheDocument()
  })

  it('un clic sur un projet le consulte, en UN clic ; le projet consulté porte aria-current', async () => {
    const consulter = vi.fn()
    render(<Cadre surConsulter={consulter} consulte="a" />)
    expect(screen.getByRole('link', { name: /réunions ami/i })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /produit/i })).not.toHaveAttribute('aria-current')
    await userEvent.click(screen.getByRole('link', { name: /produit/i }))
    expect(consulter).toHaveBeenCalledTimes(1)
    expect(consulter).toHaveBeenCalledWith('b')
  })

  it('chaque projet est un vrai lien qui porte son adresse (clic du milieu, copie du lien)', () => {
    render(<Cadre />)
    expect(screen.getByRole('link', { name: /produit/i })).toHaveAttribute('href', expect.stringContaining('projet=b'))
  })

  it('le filtre ignore la casse et les accents', async () => {
    render(<Cadre />)
    await userEvent.type(screen.getByRole('searchbox', { name: /filtrer les projets|filter projects/i }), 'elephant')
    expect(screen.getByRole('link', { name: /éléphants/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /produit/i })).not.toBeInTheDocument()
  })

  it('« Nouveau projet » appelle la création', async () => {
    const nouveau = vi.fn()
    render(<Cadre surNouveau={nouveau} />)
    await userEvent.click(screen.getByRole('button', { name: /nouveau projet|new project/i }))
    expect(nouveau).toHaveBeenCalledTimes(1)
  })

  it('replié : une colonne d\'icônes qui garde les MÊMES destinations, avec des noms accessibles', async () => {
    const consulter = vi.fn()
    render(<Cadre replie surConsulter={consulter} />)
    const lien = screen.getByRole('link', { name: /produit/i })
    expect(lien).toHaveAttribute('title', expect.stringMatching(/produit/i))
    await userEvent.click(lien)
    expect(consulter).toHaveBeenCalledWith('b')
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /déplier le panneau|expand the panel/i })).toBeInTheDocument()
  })

  it('le bouton bascule entre replié et déployé', async () => {
    const basculer = vi.fn()
    render(<Cadre surBasculer={basculer} />)
    await userEvent.click(screen.getByRole('button', { name: /replier le panneau|collapse the panel/i }))
    expect(basculer).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: /déplier le panneau|expand the panel/i })).toBeInTheDocument()
  })

  it('plus de 50 projets : la liste défile dans le panneau et le filtre reste utilisable', async () => {
    const beaucoup = [...PROJETS, ...Array.from({ length: 60 }, (_, i) => P(`p${i}`, `Projet ${i}`))]
    render(<Cadre projets={beaucoup} />)
    expect(screen.getAllByRole('link').length).toBeGreaterThan(60)
    await userEvent.type(screen.getByRole('searchbox'), 'Projet 42')
    expect(screen.getByRole('link', { name: /projet 42/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /projet 7\b/i })).not.toBeInTheDocument()
  })

  it('un projet sans réunion et un projet reçu restent consultables', async () => {
    const consulter = vi.fn()
    render(<Cadre surConsulter={consulter} />)
    await userEvent.click(screen.getByRole('link', { name: /équipe partenaire/i }))
    expect(consulter).toHaveBeenCalledWith('z1')
  })

  it('replié : chaque projet porte ses INITIALES, son nom reste le nom accessible et l\'infobulle', () => {
    render(<Cadre replie consulte="a" />)
    const nav = screen.getByRole('navigation', { name: /projets de la bibliothèque|library projects/i })
    const ra = within(nav).getByRole('link', { name: /réunions ami/i })
    expect(ra).toHaveTextContent('RA')
    expect(ra).toHaveAttribute('title', 'Réunions AMI')
    expect(ra).toHaveAttribute('aria-current', 'page')         // l'état actif reste visible
    expect(within(nav).getByRole('link', { name: /produit/i })).toHaveTextContent('PR')
    expect(within(nav).getByRole('link', { name: /éléphants/i })).toHaveTextContent('ÉL')
    // Deux projets différents ne se confondent plus : les pastilles diffèrent.
    const pastilles = [...nav.querySelectorAll('.projet-initiales')].map((e) => e.textContent)
    expect(new Set(pastilles).size).toBe(pastilles.length)
  })

  it('déplié, les projets gardent l\'icône (pas d\'initiales)', () => {
    render(<Cadre />)
    expect(document.querySelector('.projet-initiales')).toBeNull()
  })

  it('signale un projet d\'avant les comptes (sans propriétaire) comme avant dans la liste de portée', () => {
    render(<Cadre projets={[...PROJETS, { ...P('o', 'Ancien projet'), owner: null }]} />)
    expect(screen.getByRole('link', { name: /ancien projet.*(sans propriétaire|no owner)/i })).toBeInTheDocument()
  })
})

describe('le tiroir des projets (mobile)', () => {
  function Mobile() {
    const [ouvert, setOuvert] = useState(false)
    const [choix, setChoix] = useState('')
    return (
      <LangProvider>
        <button type="button" onClick={() => setOuvert(true)}>ouvrir</button>
        <p data-testid="choix">{choix}</p>
        <ProjetsTiroir ouvert={ouvert} surFermer={() => setOuvert(false)} projets={PROJETS} recus={RECUS} consulte="a"
                       surConsulter={setChoix} surNouveau={() => {}} />
      </LangProvider>
    )
  }

  it('est un dialogue modal fermé par Échap, qui rend le focus au bouton', async () => {
    render(<Mobile />)
    const bouton = screen.getByRole('button', { name: 'ouvrir' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    bouton.focus()
    await userEvent.click(bouton)
    const dialogue = screen.getByRole('dialog')
    expect(dialogue).toHaveAttribute('aria-modal', 'true')
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(bouton).toHaveFocus()
  })

  it('un toucher en dehors le ferme', async () => {
    render(<Mobile />)
    await userEvent.click(screen.getByRole('button', { name: 'ouvrir' }))
    await userEvent.click(document.querySelector('.tiroir-fond') as Element)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('choisir un projet le consulte puis ferme le tiroir', async () => {
    render(<Mobile />)
    await userEvent.click(screen.getByRole('button', { name: 'ouvrir' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('link', { name: /produit/i }))
    expect(screen.getByTestId('choix')).toHaveTextContent('b')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('piège le focus : Tab depuis le dernier élément revient au premier', async () => {
    render(<Mobile />)
    await userEvent.click(screen.getByRole('button', { name: 'ouvrir' }))
    const dialogue = screen.getByRole('dialog')
    const focalisables = dialogue.querySelectorAll<HTMLElement>('a[href], button, input')
    focalisables[focalisables.length - 1].focus()
    await userEvent.tab()
    expect(dialogue.contains(document.activeElement)).toBe(true)
  })
})
