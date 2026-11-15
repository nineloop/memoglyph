import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { TranscriptReader } from '../components/TranscriptReader'
import { AudioProvider } from '../audio/AudioProvider'
import { MiniPlayer } from '../audio/MiniPlayer'
import { getTranscript, makeReport } from '../api'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getTranscript: vi.fn(),
    makeReport: vi.fn(),
  }
})


beforeEach(() => {
  vi.mocked(getTranscript).mockReset()
  vi.mocked(makeReport).mockReset()
})


it('affiche le transcript et les correspondances de voix', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md',
    meta: {},
    body: 'Texte',
    content: '# Note',
    voices: {
      'Speaker 1': { voice_id: 3, label: 'Alice', label_source: 'manual', display: 'Alice' },
    },
  })

  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  expect(screen.getByText('Chargement…')).toBeInTheDocument()
  expect(await screen.findByText('Texte')).toBeInTheDocument()
  expect(screen.queryByText('# Note')).not.toBeInTheDocument()
  expect(screen.getByText('Alice')).toBeInTheDocument()
  expect(screen.getByText(/Speaker 1/)).toBeInTheDocument()
})


it('l\'action du compte rendu est celle que la réunion fournit : un clic la déclenche, rien ne s\'appelle en direct', async () => {
  const user = userEvent.setup()
  const action = vi.fn()
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: 'Texte', content: '# Note',
  })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()}
                           actionCompteRendu={{ libelle: 'Rédiger le compte rendu', onClick: action }} />)
  await screen.findByText('Texte')

  await user.click(screen.getByRole('button', { name: 'Rédiger le compte rendu' }))

  expect(action).toHaveBeenCalledTimes(1)
  expect(makeReport).not.toHaveBeenCalled()       // plus d'appel synchrone depuis le lecteur : le panneau « Régénérer » est le seul chemin
})


it('sans action fournie (Assistant, Recherche, Doublons), le lecteur ne propose aucun compte rendu', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: 'Texte', content: '# Note',
  })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)
  await screen.findByText('Texte')

  expect(screen.queryByRole('button', { name: /compte rendu|Régénérer|Rédiger/i })).not.toBeInTheDocument()
})


it('positionne sans autoplay le passage demandé par une citation', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {},
    body: '[00:10] Introduction\n[01:15] Décision', content: '', has_audio: true,
  })

  const { container } = render(
    <AudioProvider>
      <TranscriptReader name="note.md" documentScope="projet-1" initialTimestamp="01:15" onBack={vi.fn()} />
    </AudioProvider>,
  )

  await screen.findByText(/Décision/)
  // L'audio est GLOBAL : l'élément apparaît quand le document devient l'objet
  // d'écoute courant, et la citation l'y positionne — sans lancer la lecture.
  await waitFor(() => {
    const audio = container.querySelector('audio') as HTMLAudioElement | null
    expect(audio?.currentTime).toBe(75)
  })
  expect(screen.getByText(/Décision/).closest('.tline')).toHaveClass('on')
  expect((container.querySelector('audio') as HTMLAudioElement).paused).toBe(true)
})


/**
 * Le transcript se lit en tours de parole.
 *
 * Les corps ci-dessous reproduisent ce qu'écrit `core/writer_md.py`, profil LLM
 * compris. Ce qui est vérifié ici n'est pas la mise en page mais ce qu'elle ne
 * doit pas coûter : aucun mot du document ne disparaît au passage.
 */
const DIARISE = [
  '# réunion budget',
  '',
  '## Conversation',
  '',
  '**Type :** réunion de suivi',
  '',
  '## Transcript',
  '',
  '**Speaker 1** [00:00:01] Bonjour à tous.',
  '',
  '**Speaker 1** [00:00:09] Commençons par le budget.',
  '',
  '**Speaker 2** [00:00:20] J’ai une réserve sur la ligne 4.',
  '',
].join('\n')

function chargeDiarise(extra: Record<string, unknown> = {}) {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: DIARISE, content: '', has_audio: true, ...extra,
  })
}

it('groupe les passages consécutifs et nomme le locuteur UNE fois', async () => {
  chargeDiarise()
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  await screen.findByText(/Commençons par le budget/)
  // Deux tours pour trois passages : Speaker 1 n'est écrit qu'une fois pour
  // ses deux prises de parole consécutives.
  expect(document.querySelectorAll('.tour')).toHaveLength(2)
  expect(document.querySelectorAll('.tour-passages .tline')).toHaveLength(3)
  expect(screen.getAllByRole('heading', { name: /Speaker 1/ })).toHaveLength(1)

  // Chaque passage garde son horodatage cliquable.
  for (const mark of ['[00:00:01]', '[00:00:09]', '[00:00:20]']) {
    expect(screen.getByRole('button', { name: mark })).toBeInTheDocument()
  }
})

it('un locuteur sans nom s’affiche « Locuteur N » dans l’interface française', async () => {
  chargeDiarise()
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  await screen.findByText(/Commençons par le budget/)
  const titres = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent ?? '')
  expect(titres.some((x) => x.includes('Locuteur 1'))).toBe(true)
  expect(titres.some((x) => x.includes('Locuteur 2'))).toBe(true)
})

it('range le profil LLM dans un repli, sans le PERDRE', async () => {
  chargeDiarise()
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)
  await screen.findByText(/Bonjour à tous/)

  const repli = document.querySelector('.transcript-preambule') as HTMLDetailsElement
  expect(repli).toBeInTheDocument()
  // Replié par défaut — c'est tout l'objet du P2 — mais présent dans le DOM.
  expect(repli.open).toBe(false)
  expect(repli).toHaveTextContent('réunion de suivi')
  expect(repli).toHaveTextContent('réunion budget')
})

it('offre un rail de locuteurs qui compte et qui déplace', async () => {
  const user = userEvent.setup()
  // jsdom ne fournit pas `scrollIntoView` — c'est d'ailleurs pourquoi le
  // composant l'appelle en optionnel. Il faut donc l'installer pour l'observer,
  // et non l'espionner.
  const scroll = vi.fn()
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    value: scroll, writable: true, configurable: true,
  })
  chargeDiarise()
  const { container } = render(
    <AudioProvider>
      <TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />
    </AudioProvider>,
  )
  await screen.findByText(/Bonjour à tous/)

  const rail = screen.getByLabelText('Repères de lecture')
  expect(within(rail).getByRole('button', { name: /Speaker 1/ })).toHaveTextContent('2')
  expect(within(rail).getByRole('button', { name: /Speaker 2/ })).toHaveTextContent('1')

  await user.click(within(rail).getByRole('button', { name: /Speaker 2/ }))

  expect(scroll).toHaveBeenCalled()
  const audio = container.querySelector('audio') as HTMLAudioElement
  await waitFor(() => expect(audio.currentTime).toBe(20))
  // Se déplacer n'est pas écouter : un clic de navigation ne démarre rien.
  expect(audio.paused).toBe(true)
  expect(screen.getByText(/réserve sur la ligne 4/).closest('.tline')).toHaveClass('on')

  Reflect.deleteProperty(Element.prototype, 'scrollIntoView')
})

it('affiche le nom d’usage d’une voix SANS effacer son étiquette brute', async () => {
  chargeDiarise({
    voices: { 'Speaker 1': { voice_id: 3, label: 'Alice', label_source: 'manual', display: 'Alice' } },
  })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)
  await screen.findByText(/Bonjour à tous/)

  const tour = document.querySelector('.tour-locuteur') as HTMLElement
  expect(tour).toHaveTextContent('Alice')
  // « Alice » seule ferait perdre le lien avec la diarisation du document.
  expect(within(tour).getByText('Speaker 1')).toBeInTheDocument()
  expect(within(screen.getByLabelText('Repères de lecture')).getByText('Alice'))
    .toBeInTheDocument()
})

it('RETOMBE sur le rendu brut quand le document n’a aucun horodatage', async () => {
  // Un transcript en texte continu n'a pas de structure : en inventer une
  // serait mentir sur ce que contient le document.
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: 'Bonjour à tous. Nous commençons.', content: '',
    voices: { 'Speaker 1': { voice_id: 3, label: 'Alice', label_source: 'manual', display: 'Alice' } },
  })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  expect(await screen.findByText(/Nous commençons/)).toBeInTheDocument()
  expect(document.querySelector('.transcript-lecture')).toBeNull()
  expect(document.querySelector('pre.preview')).toBeInTheDocument()
  // Et la correspondance des voix, qui vit d'ordinaire dans le rail, reste
  // affichée ici — sinon elle disparaîtrait sans que rien ne la remplace.
  expect(document.querySelector('.voice-legend')).toHaveTextContent('Alice')
})

it('ignore le chargement tardif du document précédent', async () => {
  type Detail = Awaited<ReturnType<typeof getTranscript>>
  let resolveOld!: (value: Detail) => void
  let resolveNew!: (value: Detail) => void
  vi.mocked(getTranscript)
    .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    .mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve }))

  const { rerender } = render(<TranscriptReader name="ancien.md" documentScope="projet-1" onBack={vi.fn()} />)
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('ancien.md', 'projet-1'))
  rerender(<TranscriptReader name="nouveau.md" documentScope="projet-1" onBack={vi.fn()} />)
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('nouveau.md', 'projet-1'))

  await act(async () => resolveNew(
    { name: 'nouveau.md', meta: {}, body: 'Document nouveau', content: '' },
  ))
  expect(await screen.findByText('Document nouveau')).toBeInTheDocument()
  await act(async () => resolveOld(
    { name: 'ancien.md', meta: {}, body: 'Document ancien', content: '' },
  ))

  await waitFor(() => {
    expect(screen.queryByText('Document ancien')).not.toBeInTheDocument()
    expect(screen.getByText('Document nouveau')).toBeInTheDocument()
  })
})

it('le rail des locuteurs et des repères se déplie au bouton (replié par défaut, la CSS le montre au bureau)', async () => {
  const user = userEvent.setup()
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, content: '',
    body: '**Alice** [00:00:05] Bonjour à tous.\n\n**Bruno** [00:00:20] On fixe le prix.',
  })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  const bascule = await screen.findByRole('button', { name: 'Locuteurs et repères' })
  const contenu = document.getElementById('rail-contenu') as HTMLElement
  expect(bascule).toHaveAttribute('aria-expanded', 'false')
  expect(bascule).toHaveAttribute('aria-controls', 'rail-contenu')
  expect(contenu).not.toHaveClass('ouvert')
  await user.click(bascule)
  expect(bascule).toHaveAttribute('aria-expanded', 'true')
  expect(contenu).toHaveClass('ouvert')
  await user.click(bascule)
  expect(contenu).not.toHaveClass('ouvert')
})


it('le bloc d\'en-tête du transcript s\'appelle « Résumé, thèmes et profils des locuteurs », sans promettre un titre, en français et en anglais', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md',
    meta: {},
    body: '## 🗣️ Profil de la conversation\n\nRésumé court.\n\n**Speaker 1** [00:00:01] Bonjour',
    content: '# Note',
    voices: {},
  })

  const fr = render(<LangProvider initiale="fr"><TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} /></LangProvider>)
  expect(await screen.findByText('Résumé, thèmes et profils des locuteurs')).toBeInTheDocument()
  expect(screen.queryByText(/Titre/)).toBeNull()
  fr.unmount()

  render(<LangProvider initiale="en"><TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} /></LangProvider>)
  expect(await screen.findByText('Summary, themes and speaker profiles')).toBeInTheDocument()
  expect(screen.queryByText(/Title/)).toBeNull()
})


it('un transcript qui ne s’ouvre pas dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  vi.mocked(getTranscript).mockRejectedValue(new Error('HTTP 404: {"detail":"introuvable-brut"}'))
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)

  expect(await screen.findByText('Ce transcript n’a pas pu être ouvert.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Ce transcript n’a pas pu être ouvert.')   // annoncée
  expect(document.body.textContent).not.toMatch(/HTTP|introuvable-brut/)
})


it('ouvrir un document avec audio ne montre pas le mini-lecteur ; « Écouter » le montre', async () => {
  const user = userEvent.setup()
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: '[00:10] Introduction', content: '', has_audio: true,
  })
  render(<LangProvider><AudioProvider><TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} /><MiniPlayer /></AudioProvider></LangProvider>)
  await screen.findByText(/Introduction/)

  expect(screen.queryByRole('region', { name: 'En cours d’écoute' })).not.toBeInTheDocument()   // rien ne recouvre le contenu
  await user.click(screen.getByRole('button', { name: 'Écouter' }))

  expect(await screen.findByRole('region', { name: 'En cours d’écoute' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Écouter' })).not.toBeInTheDocument()            // le mini-lecteur porte désormais la commande
  expect(document.querySelector('.player-ecouter')).toBeInTheDocument()                        // sa place est gardée : la frise ne remonte pas
})



it('sans audio conservé, la phrase est écrite sur l’écran (pas seulement en info-bulle) ; avec audio, elle n’y est pas', async () => {
  chargeDiarise({ has_audio: false })
  const sans = render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)
  expect(await screen.findByText('Aucun audio conservé pour ce transcript.')).toBeVisible()
  sans.unmount()

  chargeDiarise({ has_audio: true })
  render(<TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />)
  await screen.findByText(/Commençons par le budget/)
  expect(screen.queryByText('Aucun audio conservé pour ce transcript.')).toBeNull()
})
