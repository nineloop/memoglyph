import CaptureCore
import Foundation
import XCTest

final class SurveillanceTests: XCTestCase {
    private let silence = [Float](repeating: 0, count: 64)
    private let voix = (0..<64).map { Float(sin(Double($0) * 0.3)) * 0.2 }
    private let musique = (0..<64).map { Float(sin(Double($0) * 0.7)) * 0.3 }

    /// Rend les alertes tampon par tampon (0,5 s chacun) et ce qui est apparu à chaque pas.
    private func suivre(_ s: inout Surveillant, _ n: Int, micro: [Float]?, systeme: [Float],
                        peripherique: String? = nil) -> [[Alerte]] {
        (0..<n).map { _ in s.observer(micro: micro, systeme: systeme, duree: 0.5, peripherique: peripherique) }
    }

    // MARK: - Micro décroché

    func testMicroAZeroExactAlerteAuSeuilEtPasAvant() {
        var s = Surveillant()
        let pas = suivre(&s, 8, micro: silence, systeme: musique)
        XCTAssertEqual(pas[0..<5].flatMap { $0 }, [], "2,5 s de zéro : encore rien")
        XCTAssertEqual(pas[5], [.microDecroche], "3 s de zéro : l'alerte")
        XCTAssertEqual(pas[6...].flatMap { $0 }, [], "une seule fois par épisode")
    }

    func testUnSonFaibleNEstPasUnMicroDecroche() {
        var s = Surveillant()
        let souffle = [Float](repeating: 0.0001, count: 64)
        XCTAssertEqual(suivre(&s, 20, micro: souffle, systeme: silence).flatMap { $0 }, [])
    }

    func testLeMicroQuiRevientRearmeLAlerte() {
        var s = Surveillant()
        _ = suivre(&s, 6, micro: silence, systeme: silence)
        _ = suivre(&s, 1, micro: voix, systeme: silence)
        let pas = suivre(&s, 6, micro: silence, systeme: silence)
        XCTAssertEqual(pas[5], [.microDecroche])
    }

    func testSansMicroAucuneAlerteMicro() {
        var s = Surveillant()
        XCTAssertEqual(suivre(&s, 20, micro: nil, systeme: musique).flatMap { $0 }, [])
    }

    func testPeripheriqueChangeUneFois() {
        var s = Surveillant(peripherique: "MacBook Pro Microphone")
        XCTAssertEqual(suivre(&s, 3, micro: voix, systeme: silence, peripherique: "MacBook Pro Microphone")
            .flatMap { $0 }, [])
        let pas = suivre(&s, 3, micro: voix, systeme: silence, peripherique: "AirPods")
        XCTAssertEqual(pas.flatMap { $0 }, [.peripheriqueChange])
    }

    // MARK: - Saturation

    func testSaturationAuSeuilEtPasAvant() {
        var s = Surveillant()
        var fort = voix
        fort[10] = 0.98
        XCTAssertEqual(s.observer(micro: fort, systeme: silence, duree: 0.5), [])
        fort[10] = 0.99
        XCTAssertEqual(s.observer(micro: fort, systeme: silence, duree: 0.5), [.saturation])
    }

    func testSaturationRepeteeNAlertePasAChaqueTampon() {
        var s = Surveillant()
        var fort = voix
        fort[10] = 1
        let pas = suivre(&s, 9, micro: fort, systeme: silence)
        XCTAssertEqual(pas.flatMap { $0 }, [.saturation], "un épisode continu : une alerte")
        _ = suivre(&s, 10, micro: voix, systeme: silence)
        XCTAssertEqual(s.observer(micro: fort, systeme: silence, duree: 0.5), [.saturation],
                       "après 5 s de calme : nouvel épisode")
    }

    // MARK: - Écho

    /// Une parole dont le niveau varie d'un tampon à l'autre, comme une voix.
    private func parole(_ k: Int, phase: Double = 0) -> Float {
        Float(0.05 + 0.2 * (sin(Double(k) * 0.3 + phase) + 1) / 2)
    }

    private func suivreEcho(_ s: inout Surveillant, _ n: Int,
                            micro: (Int) -> [Float], systeme: (Int) -> [Float]) -> [[Alerte]] {
        (0..<n).map { k in s.observer(micro: micro(k), systeme: systeme(k), duree: 0.125) }
    }

    func testEchoQuandLeMicroSuitLeSystemeAuSeuilEtPasAvant() {
        var s = Surveillant()
        let visio = { (k: Int) in self.musique.map { $0 * self.parole(k) } }
        // Le micro reprend la visio un tampon plus tard, atténuée, sur une autre forme d'onde.
        let pas = suivreEcho(&s, 60, micro: { k in self.voix.map { $0 * 0.4 * self.parole(k - 1) } }, systeme: visio)
        XCTAssertEqual(pas[0..<39].flatMap { $0 }, [], "fenêtre de 4,875 s : encore rien")
        XCTAssertEqual(pas[39], [.echo], "5 s : l'alerte")
        XCTAssertEqual(pas[40...].flatMap { $0 }, [], "une seule fois par épisode")
    }

    func testDesVoixQuiAlternentNeSontPasDeLEcho() {
        var s = Surveillant()
        let pas = suivreEcho(&s, 120, micro: { k in self.voix.map { $0 * self.parole(k, phase: .pi) } },
                             systeme: { k in self.musique.map { $0 * self.parole(k) } })
        XCTAssertEqual(pas.flatMap { $0 }, [])
    }

    func testUnNiveauConstantNEstPasDeLEcho() {
        var s = Surveillant()
        XCTAssertEqual(suivre(&s, 30, micro: voix, systeme: musique).flatMap { $0 }, [])
    }

    func testSystemeSilencieuxPasDEcho() {
        var s = Surveillant()
        let murmure = { (k: Int) in self.musique.map { $0 * self.parole(k) * 0.02 } }
        XCTAssertEqual(suivreEcho(&s, 120, micro: murmure, systeme: murmure).flatMap { $0 }, [])
    }

    func testCorrelation() {
        XCTAssertEqual(Surveillant.correlation(musique, musique), 1, accuracy: 1e-4)
        XCTAssertEqual(Surveillant.correlation(musique, musique.map { -$0 }), -1, accuracy: 1e-4)
        XCTAssertEqual(Surveillant.correlation(silence, musique), 0)
        XCTAssertEqual(Surveillant.correlation([], []), 0)
    }

    // MARK: - Pastille

    func testLaPastilleNExisteQuEnEnregistrement() {
        XCTAssertFalse(EtatPastille.cachee.visible)
        XCTAssertEqual(EtatPastille.cachee.temps(maintenant: 100), "")
        let pastille = EtatPastille.enregistrement(debut: 100)
        XCTAssertTrue(pastille.visible)
        XCTAssertEqual(pastille.temps(maintenant: 100), "00:00")
        XCTAssertEqual(pastille.temps(maintenant: 100 + 65.9), "01:05")
        XCTAssertEqual(pastille.temps(maintenant: 100 + 3723), "1:02:03")
        XCTAssertEqual(pastille.temps(maintenant: 50), "00:00", "horloge qui recule : pas de temps négatif")
    }

    // MARK: - Rappels

    func testRappelsAuDemarrage() {
        XCTAssertEqual(Rappels.auDemarrage(visio: true), [Rappels.participants, Rappels.casque])
        XCTAssertEqual(Rappels.auDemarrage(visio: false), [Rappels.usagePrive], "replay : sans micro, pas de casque")
    }

    // MARK: - Colonnes

    func testColonnesAligneesQuandUnNomDepasse() {
        let largeurs = [10, 6]
        let court = Colonnes.ligne(["Zoom", "123", "us.zoom.xos"], largeurs: largeurs)
        let long = Colonnes.ligne(["Microsoft Teams (work)", "45678", "com.microsoft"], largeurs: largeurs)
        XCTAssertEqual(court, "Zoom      123   us.zoom.xos")
        XCTAssertEqual(long, "Microsof… 45678 com.microsoft")
        XCTAssertEqual(court.distance(from: court.startIndex, to: court.range(of: "us.")!.lowerBound),
                       long.distance(from: long.startIndex, to: long.range(of: "com.")!.lowerBound))
    }

    func testUnNomQuiTientJusteNEstPasTronque() {
        XCTAssertEqual(Colonnes.ligne(["123456789", "x"], largeurs: [10]), "123456789 x")
        XCTAssertEqual(Colonnes.ligne(["1234567890", "x"], largeurs: [10]), "12345678… x")
    }

    // MARK: - Niveaux et nom du fichier

    func testNiveauEnDecibels() {
        XCTAssertEqual(Niveau.affiche(crete: 0), 0)
        XCTAssertEqual(Niveau.affiche(crete: .nan), 0)
        XCTAssertEqual(Niveau.affiche(crete: 1), 1)
        XCTAssertEqual(Niveau.affiche(crete: 2), 1, "au-delà de la pleine échelle : borné")
        XCTAssertEqual(Niveau.affiche(crete: 0.1), 2.0 / 3, accuracy: 1e-6, "-20 dB sur 60")
        XCTAssertEqual(Niveau.affiche(crete: 0.0001), 0, "-80 dB : sous la plage")
    }

    func testNomDuFichier() {
        let debut = Date(timeIntervalSince1970: 1_792_918_400)   // 2026-10-25 08:53:20 UTC
        let utc = TimeZone(identifier: "UTC")!
        XCTAssertEqual(NomCapture.fichier(visio: true, debut: debut, fuseau: utc), "visio-2026-10-25-08h5320.wav")
        XCTAssertEqual(NomCapture.fichier(visio: false, debut: debut, fuseau: utc), "replay-2026-10-25-08h5320.wav")
    }
}
