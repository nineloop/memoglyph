import Foundation

/// Erreur de lecture ou d'écriture d'un WAV, avec un message lisible.
public struct ErreurWAV: Error, Equatable, CustomStringConvertible {
    public let description: String
    public init(_ description: String) { self.description = description }
}

/// Ce que dit l'en-tête d'un WAV PCM 16 bits.
public struct EnteteWAV: Equatable {
    public let canaux: UInt16
    public let frequence: UInt32
    public let octetsDonnees: UInt32

    public init(canaux: UInt16, frequence: UInt32, octetsDonnees: UInt32) {
        self.canaux = canaux
        self.frequence = frequence
        self.octetsDonnees = octetsDonnees
    }

    public var trames: UInt64 { UInt64(octetsDonnees) / UInt64(canaux) / 2 }
}

/// WAV PCM 16 bits écrit par tranches (C7).
///
/// Chaque tranche ajoute ses échantillons en fin de fichier, PUIS réécrit les
/// tailles de l'en-tête et force l'écriture sur le disque. Un arrêt brutal (plantage,
/// coupure de courant) laisse donc un fichier lisible jusqu'à la dernière tranche ;
/// `reparer` récupère aussi ce qui a été écrit après elle.
public final class EcrivainWAV {
    public static let tailleEntete = 44

    public let url: URL
    public let canaux: UInt16
    public let frequence: UInt32
    public let tramesParTranche: Int
    public private(set) var tramesEcrites: UInt64 = 0

    private let fichier: FileHandle
    private var enAttente: [Int16] = []
    private var ferme = false

    public init(url: URL, canaux: UInt16, frequence: UInt32, tramesParTranche: Int) throws {
        guard canaux > 0, frequence > 0, tramesParTranche > 0 else {
            throw ErreurWAV("Format invalide : \(canaux) canaux, \(frequence) Hz")
        }
        // C5 : la création elle-même refuse un fichier existant (`O_EXCL`), sans
        // vérification séparée qui laisserait une course entre le test et l'écriture.
        let descripteur = open(url.path, O_RDWR | O_CREAT | O_EXCL, 0o644)
        guard descripteur >= 0 else {
            if errno == EEXIST {
                throw ErreurWAV("\(url.path) existe déjà : rien n'est écrasé, choisissez un autre nom")
            }
            throw ErreurWAV("Impossible de créer \(url.path) : \(String(cString: strerror(errno)))")
        }
        self.url = url
        self.canaux = canaux
        self.frequence = frequence
        self.tramesParTranche = tramesParTranche
        self.fichier = FileHandle(fileDescriptor: descripteur, closeOnDealloc: true)
        try fichier.write(contentsOf: EcrivainWAV.entete(canaux: canaux, frequence: frequence, octetsDonnees: 0))
    }

    deinit { try? fermer() }

    /// Ajoute des échantillons entrelacés ; écrit une tranche dès qu'elle est pleine.
    public func ajouter(_ echantillons: [Int16]) throws {
        guard !ferme else { throw ErreurWAV("Fichier déjà fermé : \(url.path)") }
        guard echantillons.count % Int(canaux) == 0 else {
            throw ErreurWAV("\(echantillons.count) échantillons pour \(canaux) canaux : trame incomplète")
        }
        enAttente.append(contentsOf: echantillons)
        while enAttente.count >= tramesParTranche * Int(canaux) {
            let tranche = Array(enAttente.prefix(tramesParTranche * Int(canaux)))
            enAttente.removeFirst(tranche.count)
            try ecrire(tranche)
        }
    }

    /// Écrit ce qui attend, même une tranche incomplète, puis met l'en-tête à jour.
    public func vider() throws {
        guard !enAttente.isEmpty else { return }
        let reste = enAttente
        enAttente.removeAll()
        try ecrire(reste)
    }

    public func fermer() throws {
        guard !ferme else { return }
        try vider()
        try fichier.close()
        ferme = true
    }

    private func ecrire(_ echantillons: [Int16]) throws {
        // Les Mac (Apple Silicon comme Intel) sont petit-boutistes, comme le WAV.
        let donnees = echantillons.withUnsafeBytes { Data($0) }
        try fichier.seekToEnd()
        try fichier.write(contentsOf: donnees)
        tramesEcrites += UInt64(echantillons.count / Int(canaux))
        try ecrireTailles(octetsDonnees: tramesEcrites * UInt64(canaux) * 2)
        try fichier.synchronize()
    }

    private func ecrireTailles(octetsDonnees: UInt64) throws {
        let tailles = EcrivainWAV.tailles(octetsDonnees: octetsDonnees)
        try fichier.seek(toOffset: 4)
        try fichier.write(contentsOf: tailles.riff)
        try fichier.seek(toOffset: 40)
        try fichier.write(contentsOf: tailles.donnees)
    }

    // MARK: - En-tête

    public static func entete(canaux: UInt16, frequence: UInt32, octetsDonnees: UInt32) -> Data {
        var d = Data()
        d.append(contentsOf: Array("RIFF".utf8))
        d.append(petitBoutiste(36 + octetsDonnees))
        d.append(contentsOf: Array("WAVEfmt ".utf8))
        d.append(petitBoutiste(UInt32(16)))
        d.append(petitBoutiste(UInt16(1)))                        // PCM
        d.append(petitBoutiste(canaux))
        d.append(petitBoutiste(frequence))
        d.append(petitBoutiste(frequence * UInt32(canaux) * 2))   // octets par seconde
        d.append(petitBoutiste(canaux * 2))                       // octets par trame
        d.append(petitBoutiste(UInt16(16)))                       // bits par échantillon
        d.append(contentsOf: Array("data".utf8))
        d.append(petitBoutiste(octetsDonnees))
        return d
    }

    public static func lireEntete(_ url: URL) throws -> EnteteWAV {
        let fichier = try FileHandle(forReadingFrom: url)
        defer { try? fichier.close() }
        guard let d = try fichier.read(upToCount: tailleEntete), d.count == tailleEntete,
              d.prefix(4) == Data("RIFF".utf8), d[8..<16] == Data("WAVEfmt ".utf8),
              d[36..<40] == Data("data".utf8),
              lire(UInt16.self, d, 20) == 1, lire(UInt16.self, d, 34) == 16 else {
            throw ErreurWAV("\(url.lastPathComponent) n'est pas un WAV PCM 16 bits écrit par Memoglyph")
        }
        return EnteteWAV(canaux: lire(UInt16.self, d, 22), frequence: lire(UInt32.self, d, 24),
                         octetsDonnees: lire(UInt32.self, d, 40))
    }

    /// Reprise d'un fichier interrompu : recale l'en-tête sur ce que le disque
    /// contient vraiment, trame incomplète de fin retirée. Rend le nombre de trames.
    @discardableResult
    public static func reparer(_ url: URL) throws -> UInt64 {
        let entete = try lireEntete(url)
        let fichier = try FileHandle(forUpdating: url)
        defer { try? fichier.close() }
        let taille = try fichier.seekToEnd()
        let octetsParTrame = UInt64(entete.canaux) * 2
        let trames = (taille - UInt64(tailleEntete)) / octetsParTrame
        let octetsDonnees = trames * octetsParTrame
        try fichier.truncate(atOffset: UInt64(tailleEntete) + octetsDonnees)
        let t = tailles(octetsDonnees: octetsDonnees)
        try fichier.seek(toOffset: 4)
        try fichier.write(contentsOf: t.riff)
        try fichier.seek(toOffset: 40)
        try fichier.write(contentsOf: t.donnees)
        try fichier.synchronize()
        return trames
    }

    private static func tailles(octetsDonnees: UInt64) -> (riff: Data, donnees: Data) {
        // Un WAV plafonne à 4 Go : au-delà, l'en-tête garde le maximum et `reparer`
        // ne peut plus rien recaler. Plus de six heures en 48 kHz stéréo 16 bits.
        let borne = UInt32(min(octetsDonnees, UInt64(UInt32.max - 36)))
        return (petitBoutiste(36 + borne), petitBoutiste(borne))
    }

    private static func petitBoutiste<T: FixedWidthInteger>(_ valeur: T) -> Data {
        withUnsafeBytes(of: valeur.littleEndian) { Data($0) }
    }

    private static func lire<T: FixedWidthInteger>(_: T.Type, _ d: Data, _ position: Int) -> T {
        var valeur: T = 0
        for i in 0..<MemoryLayout<T>.size {
            valeur |= T(d[d.startIndex + position + i]) << (8 * i)
        }
        return valeur
    }
}

/// C6 : la première erreur d'écriture arrête l'écriture tout de suite et se signale
/// une seule fois ; ce qui est déjà sur le disque reste récupérable par `reparer`.
/// Sans elle, une capture continuait des minutes à écrire dans le vide, et l'erreur
/// ne se voyait qu'à l'arrêt.
public struct GardeEcriture {
    public private(set) var erreur: Error?

    public init() {}

    /// Écrit avec `ecrire` tant qu'aucune erreur n'est survenue. Rend l'erreur la
    /// première fois qu'elle survient, `nil` sinon, et n'écrit plus rien ensuite.
    public mutating func ecrire(_ ecrire: () throws -> Void) -> Error? {
        guard erreur == nil else { return nil }
        do {
            try ecrire()
            return nil
        } catch {
            self.erreur = error
            return error
        }
    }
}
