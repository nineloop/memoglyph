import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { Project } from '../api'
import { LangProvider } from '../components/LangProvider'
import { ProjetsPanel } from '../components/ProjetsPanel'
import { RangerDans } from '../components/RangerDans'
import { translate } from '../lib/i18n'
import { libelleProjet } from '../lib/libelleProjet'

const P = (id: string, name: string, kind: Project['kind'] = 'regular'): Project =>
  ({ id, project: name, name, kind, owner: 'moi', count: 1 })
const PROJETS = [P('inbox', 'Non classé', 'inbox'), P('a', 'Réunions AMI')]

describe('« Non classé » est un libellé du système, traduit (FR-029)', () => {
  it('libelleProjet : traduit la réception, jamais un projet nommé', () => {
    const en = (k: Parameters<typeof translate>[1]) => translate('en', k)
    const fr = (k: Parameters<typeof translate>[1]) => translate('fr', k)
    expect(libelleProjet('Non classé', en, 'inbox')).toBe('Unclassified')
    expect(libelleProjet('Non classé', fr, 'inbox')).toBe('Non classé')
    expect(libelleProjet('Non classé', en)).toBe('Unclassified')  // sans le type, la constante suffit
    expect(libelleProjet('Réunions AMI', en, 'regular')).toBe('Réunions AMI')
  })

  it.each([['en', 'Unclassified'], ['fr', 'Non classé']] as const)(
    'le panneau et « Ranger dans » affichent la réception en %s', async (lang, attendu) => {
      render(<LangProvider initiale={lang}>
        <ProjetsPanel projets={PROJETS} recus={[]} consulte="inbox" replie={false}
                      surBasculer={() => {}} surConsulter={() => {}} surNouveau={() => {}} />
        <RangerDans projets={PROJETS} destination="inbox" surChoisir={() => {}} surCreer={async () => PROJETS[1]} />
      </LangProvider>)
      const nav = screen.getByRole('navigation')
      expect(within(nav).getByRole('link', { name: new RegExp(attendu) })).toBeInTheDocument()
      expect(screen.getByRole('combobox')).toHaveValue(attendu)
      await userEvent.click(screen.getByRole('combobox'))
      expect(screen.getByRole('option', { name: attendu })).toBeInTheDocument()
      if (lang === 'en') expect(screen.queryByText('Non classé')).not.toBeInTheDocument()
    })

  it('le filtre cherche dans le nom affiché', async () => {
    render(<LangProvider initiale="en">
      <RangerDans projets={PROJETS} destination="a" surChoisir={() => {}} surCreer={async () => PROJETS[1]} />
    </LangProvider>)
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'unclass')
    expect(screen.getByRole('option', { name: 'Unclassified' })).toBeInTheDocument()
  })
})


// ---------- Les autres endroits où un nom de projet atteint l'écran ----------
import { vi } from 'vitest'
import { getMeetings, getLanguages, search } from '../api'
import type { Meeting, SearchResult } from '../api'
import { AudioProvider } from '../audio/AudioProvider'
import { LibraryPanel } from '../components/LibraryPanel'
import { SearchPanel } from '../components/SearchPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getMeetings: vi.fn(), getLanguages: vi.fn(), search: vi.fn() }
})

describe('« Non classé » dans les autres écrans, en anglais', () => {
  it('la galerie de « All my projects » nomme la réception en anglais', async () => {
    vi.mocked(getLanguages).mockResolvedValue(['fr'])
    vi.mocked(getMeetings).mockResolvedValue([] as Meeting[])
    render(<LangProvider initiale="en"><AudioProvider>
      <LibraryPanel reloadKey={0} viewScope="all" projects={PROJETS} onSelectProject={() => {}} />
    </AudioProvider></LangProvider>)
    const galerie = await screen.findByRole('region', { name: /projects|projets/i })
    expect(within(galerie).getByText('Unclassified')).toBeInTheDocument()
    expect(within(galerie).queryByText('Non classé')).not.toBeInTheDocument()
  })

  it('la recherche globale étiquette une réunion de la réception « Unclassified »', async () => {
    const resultat: SearchResult = {
      name: 'a.md', document_id: 'd', meeting_id: 'm', project_id: 'inbox', project: 'Non classé',
      title: 'Comité', source: 'a.m4a', language: 'fr', duration: '10:00', diarization: true,
      profiled: false, n_speakers: 2, ts: '00:12', snippet: 'Une décision', rang: 1,
    }
    vi.mocked(search).mockResolvedValue([resultat])
    render(<LangProvider initiale="en"><SearchPanel viewScope="all" scopeLabel="All my projects"
                                                    initialQuery="decision" initialEverywhere /></LangProvider>)
    expect(await screen.findByText('Unclassified')).toBeInTheDocument()
    expect(screen.queryByText('Non classé')).not.toBeInTheDocument()
  })
})

describe('« Ranger dans » : le doublon se mesure sur le nom affiché ET le nom stocké', () => {
  it('en anglais, taper « Non classé » ne propose pas de créer un projet du même nom', async () => {
    render(<LangProvider initiale="en">
      <RangerDans projets={PROJETS} destination="a" surChoisir={() => {}} surCreer={async () => PROJETS[1]} />
    </LangProvider>)
    const champ = screen.getByRole('combobox')
    await userEvent.clear(champ)
    await userEvent.type(champ, 'Non classé')
    expect(screen.queryByRole('option', { name: /new project|nouveau projet/i })).not.toBeInTheDocument()
  })
})


describe('le pluriel anglais de zéro (« 0 meetings »)', () => {
  it('pluralKey : zéro est pluriel en anglais, singulier en français', async () => {
    const { pluralKey } = await import('../lib/i18n')
    expect(pluralKey('library.count', 0, 'en')).toBe('library.count.other')
    expect(pluralKey('library.count', 1, 'en')).toBe('library.count.one')
    expect(pluralKey('library.count', 0, 'fr')).toBe('library.count.one')
    expect(pluralKey('library.count', 0)).toBe('library.count.one')        // défaut : français
  })

  it('la galerie dit « 0 meetings » en anglais', async () => {
    vi.mocked(getLanguages).mockResolvedValue(['fr'])
    vi.mocked(getMeetings).mockResolvedValue([] as Meeting[])
    render(<LangProvider initiale="en"><AudioProvider>
      <LibraryPanel reloadKey={0} viewScope="all" projects={PROJETS.map((p) => ({ ...p, count: 0 }))} onSelectProject={() => {}} />
    </AudioProvider></LangProvider>)
    const galerie = await screen.findByRole('region', { name: /projects|projets/i })
    expect(within(galerie).getAllByText('0 meetings').length).toBeGreaterThan(0)
    expect(within(galerie).queryByText('0 meeting')).not.toBeInTheDocument()
  })
})
