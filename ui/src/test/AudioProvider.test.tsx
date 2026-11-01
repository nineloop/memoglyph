import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'

import { AudioProvider, useAudio } from '../audio/AudioProvider'

// Un harnais minimal : il expose `play` et `seek` du lecteur global, et affiche
// la seconde courante. `<audio>` n'existe qu'une fois un document chargé.
function Harnais({ startAt }: { startAt?: number }) {
  const { play, seek, currentTime } = useAudio()
  return (
    <>
      <button onClick={() => play({ name: 'a.md', scope: 'p', title: 'A' }, { autoplay: false })}>
        charger
      </button>
      <button onClick={() => seek(75)}>citer</button>
      <button onClick={() => seek(75, { autoplay: true })}>citer-et-jouer</button>
      {startAt !== undefined && (
        <button onClick={() => play({ name: 'a.md', scope: 'p', title: 'A' }, { startAt })}>
          rejouer-a
        </button>
      )}
      <span data-testid="t">{currentTime}</span>
    </>
  )
}

function render_() {
  return render(
    <AudioProvider>
      <Harnais />
    </AudioProvider>,
  )
}

function audioEl(container: HTMLElement) {
  return container.querySelector('audio') as HTMLAudioElement
}

beforeEach(() => localStorage.clear())


it('une position explicite est appliquée TOUT DE SUITE', () => {
  const { container } = render_()
  fireEvent.click(screen.getByText('charger'))
  fireEvent.click(screen.getByText('citer'))
  expect(audioEl(container).currentTime).toBe(75)
  expect(screen.getByTestId('t').textContent).toBe('75')
})


it('une citation prime sur la reprise sauvegardée quand les métadonnées arrivent APRÈS', () => {
  // Reprise persistée à 200 s ; la citation vise 75 s. Le média (re)charge
  // ensuite — `loadedmetadata` arrive après le positionnement. Sans le
  // correctif, `seek` effaçait l'intention et la reprise (200) l'emportait.
  localStorage.setItem('memoglyph.pos:a.md', '200')
  const { container } = render_()
  fireEvent.click(screen.getByText('charger'))
  fireEvent.click(screen.getByText('citer'))

  act(() => { audioEl(container).dispatchEvent(new Event('loadedmetadata')) })

  expect(audioEl(container).currentTime).toBe(75) // la citation, pas 200
})


it('sans position explicite, la reprise sauvegardée s’applique normalement', () => {
  // Le pendant : le correctif ne doit pas confisquer la reprise. Aucun `seek`
  // ici — juste l'ouverture du document — donc `loadedmetadata` restaure 200.
  localStorage.setItem('memoglyph.pos:a.md', '200')
  const { container } = render_()
  fireEvent.click(screen.getByText('charger'))

  act(() => { audioEl(container).dispatchEvent(new Event('loadedmetadata')) })

  expect(audioEl(container).currentTime).toBe(200)
})


it('un durationchange après une citation ne ramène PAS la tête de lecture', () => {
  // Le défaut réel observé au navigateur : `loadedmetadata` ET `durationchange`
  // pointaient sur le même gestionnaire ; `durationchange` (qui peut se répéter)
  // rejouait la reprise (200) et écrasait la citation (75) déjà posée. Désormais
  // `durationchange` ne met à jour QUE la durée.
  localStorage.setItem('memoglyph.pos:a.md', '200')
  const { container } = render_()
  fireEvent.click(screen.getByText('charger'))
  fireEvent.click(screen.getByText('citer'))

  act(() => { audioEl(container).dispatchEvent(new Event('loadedmetadata')) })
  expect(audioEl(container).currentTime).toBe(75)

  act(() => { audioEl(container).dispatchEvent(new Event('durationchange')) })
  expect(audioEl(container).currentTime).toBe(75) // toujours la citation
})


it('l’intention est CONSOMMÉE : un rechargement ultérieur reprend la position sauvegardée', () => {
  // Après qu'une citation a été appliquée au chargement, elle ne doit pas se
  // rejouer indéfiniment : un `loadedmetadata` suivant retombe sur la reprise.
  localStorage.setItem('memoglyph.pos:a.md', '200')
  const { container } = render_()
  fireEvent.click(screen.getByText('charger'))
  fireEvent.click(screen.getByText('citer'))

  act(() => { audioEl(container).dispatchEvent(new Event('loadedmetadata')) })
  expect(audioEl(container).currentTime).toBe(75)

  // Deuxième chargement, sans nouvelle intention : la reprise reprend la main.
  act(() => { audioEl(container).dispatchEvent(new Event('loadedmetadata')) })
  expect(audioEl(container).currentTime).toBe(200)
})
