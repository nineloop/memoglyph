import CaptureCore
import CoreAudio
import Foundation

/// Enregistre le son de processus choisis et, en option, le micro par défaut.
///
/// Un process tap (C2) capte le mélange stéréo des processus visés, sans pilote
/// virtuel. Le tap et le micro entrent dans un même appareil agrégé privé : une
/// seule horloge, donc micro et système restent alignés trame à trame. Dans les
/// tampons d'entrée de l'agrégat, ceux du micro viennent d'abord, ceux du tap en
/// dernier ; les canaux du tap se reconnaissent donc à partir de la fin.
public final class Enregistreur {
    public struct Bilan {
        public let trames: UInt64
        public let frequence: UInt32
        public let creteMicro: Float?
        public let creteSysteme: Float
        public var duree: Double { Double(trames) / Double(frequence) }
    }

    private let processus: [AudioObjectID]
    private let global: Bool
    private let micro: Bool
    private let sortie: URL
    private let file = DispatchQueue(label: "org.memoglyph.capture.audio")

    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var agregat = AudioObjectID(kAudioObjectUnknown)
    private var procedure: AudioDeviceIOProcID?
    private var canauxTap = 2
    private var ecrivain: EcrivainWAV?
    private var creteMicro: Float = 0
    private var creteSysteme: Float = 0
    private var garde = GardeEcriture()

    public private(set) var frequence: UInt32 = 0

    /// Appelé sur la file audio à chaque tampon reçu : micro (nil sans micro), système,
    /// durée en secondes. À poser avant `demarrer()` ; il doit rendre la main vite.
    public var surTampon: ((_ micro: [Float]?, _ systeme: [Float], _ duree: Double) -> Void)?

    /// Appelé une seule fois, hors de la file audio, à la première erreur d'écriture (C6) :
    /// l'écriture s'arrête aussitôt ; l'appelant arrête la capture (`arreter()` rend l'erreur).
    /// À poser avant `demarrer()`.
    public var surErreur: ((Error) -> Void)?

    /// Nom du micro par défaut de macOS, pour repérer un changement en cours de prise.
    public static func microParDefaut() -> String? {
        guard let appareil = try? lireValeur(objetSysteme, kAudioHardwarePropertyDefaultInputDevice,
                                             AudioObjectID(kAudioObjectUnknown)),
              appareil != kAudioObjectUnknown else { return nil }
        return try? lireChaine(appareil, kAudioObjectPropertyName)
    }

    /// `global` : tout le son du Mac, sauf `processus` (le compagnon lui-même) ; sinon le
    /// son de `processus` seuls.
    public init(processus: [UInt32], micro: Bool, sortie: URL, global: Bool = false) {
        self.processus = processus.map { AudioObjectID($0) }
        self.global = global
        self.micro = micro
        self.sortie = sortie
    }

    deinit { liberer() }

    public func demarrer() throws {
        do {
            try creerTap()
            try creerAgregat()
            frequence = UInt32(try lireValeur(agregat, kAudioDevicePropertyNominalSampleRate, Float64(0)))
            guard frequence > 0 else { throw ErreurCoreAudio(quoi: "fréquence de l'appareil agrégé", statut: -1) }
            // Une tranche par seconde : au pire une seconde perdue sur un arrêt brutal.
            ecrivain = try EcrivainWAV(url: sortie, canaux: 2, frequence: frequence,
                                       tramesParTranche: Int(frequence))
            try verifier(AudioDeviceCreateIOProcIDWithBlock(&procedure, agregat, file) { [weak self] _, entree, _, _, _ in
                self?.recevoir(entree)
            }, "création de la procédure d'entrée")
            try verifier(AudioDeviceStart(agregat, procedure), "démarrage de la capture")
        } catch {
            liberer()
            throw error
        }
    }

    public func arreter() throws -> Bilan {
        if let procedure { AudioDeviceStop(agregat, procedure) }
        let trames: UInt64 = try file.sync {
            if let erreur = garde.erreur { throw erreur }
            try ecrivain?.fermer()
            return ecrivain?.tramesEcrites ?? 0
        }
        liberer()
        return Bilan(trames: trames, frequence: frequence,
                     creteMicro: micro ? creteMicro : nil, creteSysteme: creteSysteme)
    }

    // MARK: - Mise en place

    private func creerTap() throws {
        let description = global ? CATapDescription(stereoGlobalTapButExcludeProcesses: processus)
            : CATapDescription(stereoMixdownOfProcesses: processus)
        description.uuid = UUID()
        description.name = "Memoglyph capture"
        description.isPrivate = true
        description.muteBehavior = .unmuted   // l'utilisateur continue d'entendre la visio
        try verifier(AudioHardwareCreateProcessTap(description, &tap), "création du process tap")
        let format = try lireValeur(tap, kAudioTapPropertyFormat, AudioStreamBasicDescription())
        guard format.mFormatID == kAudioFormatLinearPCM,
              format.mFormatFlags & kAudioFormatFlagIsFloat != 0, format.mBitsPerChannel == 32 else {
            throw ErreurCoreAudio(quoi: "format du tap inattendu (\(format.mFormatID), \(format.mBitsPerChannel) bits)",
                                  statut: -1)
        }
        canauxTap = Int(format.mChannelsPerFrame)
    }

    private func creerAgregat() throws {
        let selecteur = micro ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice
        let appareil = try lireValeur(objetSysteme, selecteur, AudioObjectID(kAudioObjectUnknown))
        let uid = try lireChaine(appareil, kAudioDevicePropertyDeviceUID)
        let tapUID = try lireChaine(tap, kAudioTapPropertyUID)
        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Memoglyph capture",
            kAudioAggregateDeviceUIDKey: "org.memoglyph.capture.\(UUID().uuidString)",
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceMainSubDeviceKey: uid,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: uid]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                               kAudioSubTapUIDKey: tapUID]],
        ]
        try verifier(AudioHardwareCreateAggregateDevice(description as CFDictionary, &agregat),
                     "création de l'appareil agrégé")
    }

    private func liberer() {
        if let procedure {
            AudioDeviceDestroyIOProcID(agregat, procedure)
            self.procedure = nil
        }
        if agregat != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(agregat)
            agregat = AudioObjectID(kAudioObjectUnknown)
        }
        if tap != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tap)
            tap = AudioObjectID(kAudioObjectUnknown)
        }
    }

    // MARK: - Réception (sur `file`)

    private func recevoir(_ entree: UnsafePointer<AudioBufferList>) {
        let tampons = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: entree))
        // Les tampons du tap sont les derniers, jusqu'à couvrir ses canaux.
        var debutTap = tampons.count
        var restant = canauxTap
        while debutTap > 0 && restant > 0 {
            debutTap -= 1
            restant -= Int(tampons[debutTap].mNumberChannels)
        }
        let systeme = mono(tampons, debutTap..<tampons.count)
        let voix = micro ? mono(tampons, 0..<debutTap) : nil
        creteSysteme = max(creteSysteme, Melange.crete(systeme))
        if let voix { creteMicro = max(creteMicro, Melange.crete(voix)) }
        if let surTampon, frequence > 0 { surTampon(voix, systeme, Double(systeme.count) / Double(frequence)) }
        if let erreur = garde.ecrire({ try ecrivain?.ajouter(Melange.entrelacer(micro: voix, systeme: systeme)) }),
           let surErreur {
            DispatchQueue.global().async { surErreur(erreur) }
        }
    }

    /// Moyenne mono des tampons `indices` (Float32 entrelacés dans chaque tampon).
    private func mono(_ tampons: UnsafeMutableAudioBufferListPointer, _ indices: Range<Int>) -> [Float] {
        var somme: [Float] = []
        for i in indices {
            let tampon = tampons[i]
            let canaux = Int(tampon.mNumberChannels)
            guard canaux > 0, let donnees = tampon.mData else { continue }
            let n = Int(tampon.mDataByteSize) / MemoryLayout<Float>.size
            let echantillons = UnsafeBufferPointer(start: donnees.assumingMemoryBound(to: Float.self), count: n)
            let m = Melange.mono(echantillons, canaux: canaux)
            if somme.isEmpty {
                somme = m
            } else {
                for t in 0..<min(somme.count, m.count) { somme[t] += m[t] }
            }
        }
        let n = Float(max(indices.count, 1))
        return indices.count > 1 ? somme.map { $0 / n } : somme
    }
}
