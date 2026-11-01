import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

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
      <button onClick={() => play({ name: 'a.md', scope: 'p', title: 'A' }, { autoplay: false })}>
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

  // Ouvrir un document avec audio le charge sans le lancer : le lecteur est sa commande.
  fireEvent.click(screen.getByText('ouvrir'))
  expect(screen.getByRole('region')).toHaveClass('mini-player')
  expect(document.documentElement.dataset.lecteur).toBe('')

  fireEvent.click(screen.getByText('fermer'))
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
  expect(document.documentElement.dataset.lecteur).toBeUndefined()
})
