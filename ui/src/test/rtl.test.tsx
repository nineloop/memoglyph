/**
 * Le sens d'écriture — mesuré sur le rendu, pas sur la source.
 *
 * Il n'y avait AUCUN `dir` dans `ui/src` : un transcript arabe s'affichait de
 * gauche à droite, donc à l'envers. La transcription pouvait être parfaite, le
 * document restait inutilisable.
 *
 * `dir="auto"` plutôt qu'une direction imposée : le navigateur tranche sur le
 * PREMIER CARACTÈRE FORT de chaque bloc. Un corpus réellement mixte — mesuré
 * ici, un message franco-darija — s'affiche donc bloc par bloc dans le bon
 * sens, sans détection de langue ni question posée à l'utilisateur.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MarkdownView } from '../components/MarkdownView'

const ARABE = 'جلالة الملك نصره الله أكد أن ما حققه المغرب'
const FRANCAIS = 'Le Roi a confirmé ce que le Maroc a accompli'

describe('sens d’écriture', () => {
  it('laisse le navigateur trancher sur le corps Markdown', () => {
    const { container } = render(<MarkdownView content={ARABE} />)
    const article = container.querySelector('article.markdown-view')
    expect(article).not.toBeNull()
    expect(article!.getAttribute('dir')).toBe('auto')
  })

  it('ne fige JAMAIS une direction : ni rtl ni ltr en dur', () => {
    // Une direction imposée coucherait à l'envers la moitié d'un plateau à
    // interprètes. C'est le piège que `auto` évite, et il faut qu'il le reste.
    const { container } = render(<MarkdownView content={`${ARABE}\n\n${FRANCAIS}`} />)
    const figes = container.querySelectorAll('[dir="rtl"], [dir="ltr"]')
    expect(figes.length).toBe(0)
  })

  it('rend les deux langues dans le même document', () => {
    render(<MarkdownView content={`${ARABE}\n\n${FRANCAIS}`} />)
    expect(screen.getByText(ARABE)).toBeInTheDocument()
    expect(screen.getByText(FRANCAIS)).toBeInTheDocument()
  })
})
