import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import {
  Button, Chip, Badge, SpeakerDot, WaveBars, SegmentedControl, speakerTint,
} from '../design/primitives'

describe('primitives de l\'interface', () => {
  it('Button rend son libellé et bloque le clic quand il est désactivé', () => {
    const onClick = vi.fn()
    render(<Button variant="primary" onClick={onClick} disabled>Lancer le traitement</Button>)
    const b = screen.getByRole('button', { name: 'Lancer le traitement' })
    expect(b).toBeDisabled()
    fireEvent.click(b)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('Chip expose son état pressé', () => {
    render(<Chip active>Récent</Chip>)
    expect(screen.getByRole('button', { name: 'Récent' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('Badge applique le ton demandé', () => {
    render(<Badge tone="warn">2 réserves</Badge>)
    expect(screen.getByText('2 réserves').className).toContain('kw-badge--warn')
  })

  it('SpeakerDot montre l’initiale et une teinte stable et cyclique', () => {
    render(<SpeakerDot index={0} label="Alice" />)
    expect(screen.getByText('A').className).toContain('kw-spk--s1')
    expect(speakerTint(0)).toBe('s1')
    expect(speakerTint(6)).toBe('s1')
    expect(speakerTint(1)).toBe('s2')
  })

  it('WaveBars rend le nombre de barres demandé et marque la partie lue', () => {
    const { container } = render(<WaveBars bars={10} seed={2} played={0.5} />)
    expect(container.querySelectorAll('.kw-wave i')).toHaveLength(10)
    expect(container.querySelectorAll('.kw-wave i.is-played')).toHaveLength(5)
  })

  it('SegmentedControl remonte la valeur choisie', () => {
    const onChange = vi.fn()
    render(
      <SegmentedControl
        ariaLabel="Mode"
        value="a"
        onChange={onChange}
        options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'B' }))
    expect(onChange).toHaveBeenCalledWith('b')
  })
})
