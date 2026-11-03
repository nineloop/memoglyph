import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../api'
import {
  destinationParDefaut, destinationsPossibles, filtrerProjets,
  lireDestination, lirePanneauReplie, ecrireDestination, ecrirePanneauReplie,
  reduireNavigation,
} from '../lib/navigationEtat'
import type { EtatNavigation } from '../lib/navigationEtat'

const P = (id: string, name: string, kind: Project['kind'] = 'regular', count = 1): Project =>
  ({ id, project: name, name, kind, owner: 'moi', count })
const projets = [P('inbox', 'Non classé', 'inbox', 0), P('a', 'Réunions AMI', 'regular', 5), P('b', 'Produit'), P('c', 'Éléphants')]

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('le projet consulté et la destination sont deux états distincts (SC-002)', () => {
  const depart: EtatNavigation = { consulte: 'a', destination: 'b' }

  it('changer la destination ne modifie JAMAIS le projet consulté', () => {
    const apres = reduireNavigation(depart, { type: 'destiner', projet: 'c' })
    expect(apres.destination).toBe('c')
    expect(apres.consulte).toBe('a')
  })

  it('changer le projet consulté ne modifie JAMAIS la destination', () => {
    const apres = reduireNavigation(depart, { type: 'consulter', projet: 'all' })
    expect(apres.consulte).toBe('all')
    expect(apres.destination).toBe('b')
  })

  it("ne renvoie pas un nouvel objet quand rien ne change", () => {
    expect(reduireNavigation(depart, { type: 'consulter', projet: 'a' })).toBe(depart)
    expect(reduireNavigation(depart, { type: 'destiner', projet: 'b' })).toBe(depart)
  })
})

describe('la destination proposée', () => {
  it('reprend la dernière destination mémorisée quand elle existe encore', () => {
    expect(destinationParDefaut(projets, 'b', 'inbox')).toBe('b')
  })

  it('retombe sur le « Non classé » quand rien n\'est mémorisé', () => {
    expect(destinationParDefaut(projets, null, 'inbox')).toBe('inbox')
  })

  it('retombe sur le « Non classé » quand le projet mémorisé a disparu ou n\'est plus une destination', () => {
    expect(destinationParDefaut(projets, 'supprime', 'inbox')).toBe('inbox')
  })

  it('exclut de la liste de destinations les projets sans identifiant ; les projets reçus n\'y sont jamais', () => {
    const avecSansId = [...projets, { ...P('x', 'Ancien'), id: null }]
    expect(destinationsPossibles(avecSansId).map((p) => p.id)).toEqual(['inbox', 'a', 'b', 'c'])
  })
})

describe('le filtre du panneau', () => {
  it('ignore la casse et les accents', () => {
    expect(filtrerProjets(projets, 'elephant').map((p) => p.id)).toEqual(['c'])
    expect(filtrerProjets(projets, 'AMI').map((p) => p.id)).toEqual(['a'])
    expect(filtrerProjets(projets, 'non classe').map((p) => p.id)).toEqual(['inbox'])
  })

  it('un texte vide ne filtre rien', () => {
    expect(filtrerProjets(projets, '  ')).toHaveLength(4)
  })
})

describe('la mémoire du navigateur', () => {
  it('le panneau replié est mémorisé PAR COMPTE', () => {
    ecrirePanneauReplie('alice', true)
    expect(lirePanneauReplie('alice')).toBe(true)
    expect(lirePanneauReplie('bob')).toBe(false)
  })

  it('la destination est mémorisée par compte', () => {
    ecrireDestination('alice', 'b')
    expect(lireDestination('alice')).toBe('b')
    expect(lireDestination('bob')).toBeNull()
  })

  it('sans stockage disponible, rien ne casse : valeurs par défaut', () => {
    const casse = { getItem: () => { throw new Error('indisponible') }, setItem: () => { throw new Error('indisponible') },
      removeItem: () => {}, clear: () => {}, key: () => null, length: 0 } as unknown as Storage
    vi.stubGlobal('localStorage', casse)
    expect(lirePanneauReplie('alice')).toBe(false)
    expect(lireDestination('alice')).toBeNull()
    expect(() => ecrirePanneauReplie('alice', true)).not.toThrow()
    expect(() => ecrireDestination('alice', 'b')).not.toThrow()
  })

  it('une valeur illisible est ignorée', () => {
    localStorage.setItem('memoglyph.panneau.replie.alice', 'peut-être')
    expect(lirePanneauReplie('alice')).toBe(false)
  })
})
