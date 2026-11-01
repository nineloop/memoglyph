/// Mise en forme des échantillons pour le WAV à deux canaux (C3) : micro à
/// gauche, son de l'application à droite. Les garder séparés laisse à Memoglyph
/// l'annulation d'écho (C4) et la piste « propriétaire » (C14).
public enum Melange {
    /// Ramène `canaux` canaux entrelacés à un mono, par moyenne.
    public static func mono(_ entrelace: UnsafeBufferPointer<Float>, canaux: Int) -> [Float] {
        guard canaux > 1 else { return Array(entrelace) }
        let trames = entrelace.count / canaux
        var sortie = [Float](repeating: 0, count: trames)
        for t in 0..<trames {
            var somme: Float = 0
            for c in 0..<canaux { somme += entrelace[t * canaux + c] }
            sortie[t] = somme / Float(canaux)
        }
        return sortie
    }

    public static func mono(_ entrelace: [Float], canaux: Int) -> [Float] {
        entrelace.withUnsafeBufferPointer { mono($0, canaux: canaux) }
    }

    /// Entrelace micro (gauche) et système (droite) en PCM 16 bits.
    ///
    /// Le nombre de trames est celui du système : sans micro (`nil`, mode replay)
    /// ou s'il en manque, la gauche est complétée par du silence.
    public static func entrelacer(micro: [Float]?, systeme: [Float]) -> [Int16] {
        var sortie = [Int16](repeating: 0, count: systeme.count * 2)
        for t in 0..<systeme.count {
            if let micro, t < micro.count { sortie[2 * t] = versInt16(micro[t]) }
            sortie[2 * t + 1] = versInt16(systeme[t])
        }
        return sortie
    }

    /// Flottant [-1, 1] vers 16 bits, saturé plutôt que replié au-delà.
    public static func versInt16(_ x: Float) -> Int16 {
        guard x.isFinite else { return 0 }
        let borne = max(-1, min(1, x))
        return Int16((borne * Float(Int16.max)).rounded())
    }

    /// Crête absolue, pour savoir si un canal est resté muet.
    public static func crete(_ echantillons: [Float]) -> Float {
        echantillons.reduce(0) { max($0, abs($1)) }
    }
}
