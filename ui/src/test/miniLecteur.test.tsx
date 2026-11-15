import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { AudioProvider, useAudio } from '../audio/AudioProvider'
import { MiniPlayer } from '../audio/MiniPlayer'
import { LangProvider } from '../components/LangProvider'

// Le mini-lecteur flotte au-dessus du dock : visible, il recouvrait la fin du contenu.
// Il marque désormais la racine, et la mise en page réserve sa place
// (`:root[data-lecteur] .app-main`).
function Harnais() {
  const { play, close } = useAudio()
  return (
    <>
      <button onClick={() => play({ name: 'a.md', scope: 'p', title: 'A' }, { autoplay: true })}>
        ouvrir
      </button>
      <button onClick={close}>fermer</button>
      <MiniPlayer />
    </>
  )
}

it('visible, le mini-lecteur réserve sa place sous le contenu ; fermé, il la rend', () => {
  render(<LangProvider><AudioProvider><Harnais /></AudioProvider></LangProvider>)
  expect(document.documentElement.dataset.lecteur).toBeUndefined()

  // Demander l'écoute montre le lecteur (ouvrir un document avec audio ne le montre plus : voir TranscriptReader.test).
  fireEvent.click(screen.getByText('ouvrir'))
  expect(screen.getByRole('region')).toHaveClass('mini-player')
  expect(document.documentElement.dataset.lecteur).toBe('')

  fireEvent.click(screen.getByText('fermer'))
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
  expect(document.documentElement.dataset.lecteur).toBeUndefined()
})


// --- Un fichier qui ne se lit pas : le lecteur le dit au lieu de rester muet --------------------------------------------
function HarnaisEchec() {
  const { play, seek, close, erreur } = useAudio()
  return (
    <>
      <span data-testid="erreur">{String(erreur)}</span>
      <button onClick={() => play({ name: 'a.md', scope: 'p', title: 'A' }, { autoplay: false })}>charger-a</button>
      <button onClick={() => play({ name: 'b.md', scope: 'p', title: 'B' }, { autoplay: false })}>charger-b</button>
      <button onClick={() => seek(75, { autoplay: true })}>jouer</button>
      <button onClick={close}>fermer-tout</button>
      <MiniPlayer />
    </>
  )
}

function monterEchec(langue: 'fr' | 'en' = 'fr') {
  return render(<LangProvider initiale={langue}><AudioProvider><HarnaisEchec /></AudioProvider></LangProvider>)
}

const lecteur = (conteneur: HTMLElement) => conteneur.querySelector('audio') as HTMLAudioElement

it('une erreur de l’élément audio est dite dans le mini-lecteur, qui reste fermable et passe en pause', () => {
  const { container } = monterEchec()
  fireEvent.click(screen.getByText('charger-a'))
  fireEvent.click(screen.getByText('jouer'))                      // l'écoute est demandée : le lecteur apparaît
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByRole('slider')).toBeInTheDocument()

  act(() => { lecteur(container).dispatchEvent(new Event('error')) })

  expect(screen.getByRole('alert')).toHaveTextContent('Impossible de lire cet audio.')
  expect(screen.queryByRole('slider')).toBeNull()                  // le défileur ne ment pas sur une position
  expect(screen.getByRole('button', { name: 'Reprendre la lecture' })).toBeInTheDocument()   // en pause
  fireEvent.click(screen.getByRole('button', { name: 'Fermer le lecteur' }))
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
})

it('une lecture refusée parce que le format n’est pas pris en charge est un échec', async () => {
  const { container } = monterEchec()
  fireEvent.click(screen.getByText('charger-a'))
  lecteur(container).play = vi.fn().mockRejectedValue(new DOMException('format', 'NotSupportedError'))

  fireEvent.click(screen.getByText('jouer'))

  expect(await screen.findByRole('alert')).toHaveTextContent('Impossible de lire cet audio.')
})

it.each(['AbortError', 'NotAllowedError'])('un refus « %s » n’est pas un fichier illisible : aucune phrase d’échec', async (nom) => {
  const { container } = monterEchec()
  fireEvent.click(screen.getByText('charger-a'))
  const jouer = vi.fn().mockRejectedValue(new DOMException('refus', nom))
  lecteur(container).play = jouer

  fireEvent.click(screen.getByText('jouer'))
  await waitFor(() => expect(jouer).toHaveBeenCalled())
  await new Promise((fin) => setTimeout(fin, 20))

  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByRole('slider')).toBeInTheDocument()
})

it('l’échec disparaît quand une autre piste est choisie, quand les métadonnées d’un chargement arrivent et à la fermeture', () => {
  const { container } = monterEchec()
  fireEvent.click(screen.getByText('charger-a'))
  act(() => { lecteur(container).dispatchEvent(new Event('error')) })
  expect(screen.getByRole('alert')).toBeInTheDocument()

  fireEvent.click(screen.getByText('charger-b'))                    // une autre piste
  expect(screen.queryByRole('alert')).toBeNull()

  act(() => { lecteur(container).dispatchEvent(new Event('error')) })
  expect(screen.getByRole('alert')).toBeInTheDocument()
  act(() => { lecteur(container).dispatchEvent(new Event('loadedmetadata')) })   // le média se charge enfin
  expect(screen.queryByRole('alert')).toBeNull()

  act(() => { lecteur(container).dispatchEvent(new Event('error')) })
  expect(screen.getByTestId('erreur').textContent).toBe('true')
  fireEvent.click(screen.getByText('fermer-tout'))                  // fermé : l'état exposé par le lecteur ne garde plus l'échec
  expect(screen.getByTestId('erreur').textContent).toBe('false')
  fireEvent.click(screen.getByText('charger-a'))                    // puis rouvert : plus d'échec non plus
  expect(screen.queryByRole('alert')).toBeNull()
})

it('en anglais, la phrase d’échec est en anglais', () => {
  const { container } = monterEchec('en')
  fireEvent.click(screen.getByText('charger-a'))
  act(() => { lecteur(container).dispatchEvent(new Event('error')) })
  expect(screen.getByRole('alert')).toHaveTextContent('This audio cannot be played.')
})


it('un fichier illisible montre l’erreur dans le lecteur même sans écoute demandée', () => {
  const { container } = monterEchec()
  fireEvent.click(screen.getByText('charger-a'))
  expect(screen.queryByRole('region')).not.toBeInTheDocument()     // ouvert sans écoute : rien n'est affiché

  act(() => { lecteur(container).dispatchEvent(new Event('error')) })

  expect(screen.getByRole('alert')).toHaveTextContent('Impossible de lire cet audio.')   // l'échec ne reste pas muet
})

