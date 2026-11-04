import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : on garde la RÈGLE (la preuve visuelle vient des captures).
// Les tuiles du sélecteur « Fournisseur cloud » n'ont pas de pastille d'icône : leur seul `<span>` porte le nom et le modèle,
// et la règle générale ne vise que le SECOND `<span>` (`:nth-of-type(2)`). Sans cette règle, « OpenRouter » et « auteur/modele »
// se collent (« OpenRouterauteur/modele »).
describe('CSS des tuiles du fournisseur cloud', () => {
  it('le nom et le modèle de la tuile s’empilent, sans toucher aux autres tuiles', () => {
    expect(css).toMatch(/\.cloud-provider-select \.option-tile > span:only-of-type\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/)
  })

  it('le texte des tuiles est aligné à gauche (un bouton centre son texte par défaut)', () => {
    expect(css).toMatch(/\.cloud-provider-select \.option-tile\s*\{[^}]*text-align:\s*left/)
    expect(css).toMatch(/\.cloud-provider-select \.option-tile\s*\{[^}]*font:\s*inherit/)    // la police du bouton suit celle de la page
  })

  it('ancre : la règle générale des autres tuiles est toujours celle du second span', () => {
    expect(css).toMatch(/\.option-tile > span:nth-of-type\(2\)\s*\{[^}]*flex-direction:\s*column/)
  })
})
