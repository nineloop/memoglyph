import CaptureCore
import Foundation
import XCTest

final class EcrivainWAVTests: XCTestCase {
    private var dossier: URL!

    override func setUpWithError() throws {
        dossier = FileManager.default.temporaryDirectory
            .appendingPathComponent("memoglyph-capture-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dossier, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dossier)
    }

    private func echantillons(trames: Int, depart: Int16 = 1) -> [Int16] {
        (0..<(trames * 2)).map { Int16(truncatingIfNeeded: Int(depart) + $0) }
    }

    private func donnees(_ url: URL) throws -> Data {
        try Data(contentsOf: url).dropFirst(EcrivainWAV.tailleEntete)
    }

    // MARK: - Écriture par tranches

    func testUneTranchePleineEstSurLeDisqueAvantLaFermeture() throws {
        let url = dossier.appendingPathComponent("tranches.wav")
        let ecrivain = try EcrivainWAV(url: url, canaux: 2, frequence: 48_000, tramesParTranche: 4)

        try ecrivain.ajouter(echantillons(trames: 3))
        XCTAssertEqual(try EcrivainWAV.lireEntete(url).trames, 0, "une tranche incomplète attend")

        try ecrivain.ajouter(echantillons(trames: 6, depart: 7))
        // 9 trames reçues : deux tranches de 4 écrites, une trame en attente.
        let entete = try EcrivainWAV.lireEntete(url)
        XCTAssertEqual(entete, EnteteWAV(canaux: 2, frequence: 48_000, octetsDonnees: 8 * 2 * 2))
        XCTAssertEqual(try donnees(url).count, 8 * 2 * 2)
        XCTAssertEqual(ecrivain.tramesEcrites, 8)

        try ecrivain.fermer()
        XCTAssertEqual(try EcrivainWAV.lireEntete(url).trames, 9)
    }

    func testLEnteteEstUnWAVPCMStandard() throws {
        let entete = EcrivainWAV.entete(canaux: 2, frequence: 48_000, octetsDonnees: 400)

        XCTAssertEqual(entete.count, 44)
        XCTAssertEqual(String(decoding: entete.prefix(4), as: UTF8.self), "RIFF")
        XCTAssertEqual(Array(entete[4..<8]), [0xB4, 0x01, 0x00, 0x00])            // 36 + 400 = 436
        XCTAssertEqual(String(decoding: entete[8..<16], as: UTF8.self), "WAVEfmt ")
        XCTAssertEqual(Array(entete[20..<24]), [0x01, 0x00, 0x02, 0x00])          // PCM, 2 canaux
        XCTAssertEqual(Array(entete[24..<28]), [0x80, 0xBB, 0x00, 0x00])          // 48 000 Hz
        XCTAssertEqual(Array(entete[28..<32]), [0x00, 0xEE, 0x02, 0x00])          // 192 000 o/s
        XCTAssertEqual(Array(entete[32..<36]), [0x04, 0x00, 0x10, 0x00])          // 4 o/trame, 16 bits
        XCTAssertEqual(Array(entete[40..<44]), [0x90, 0x01, 0x00, 0x00])          // 400 octets
    }

    func testLesEchantillonsSontEcritsDansLOrdreEtEnPetitBoutiste() throws {
        let url = dossier.appendingPathComponent("ordre.wav")
        let ecrivain = try EcrivainWAV(url: url, canaux: 2, frequence: 16_000, tramesParTranche: 1)

        try ecrivain.ajouter([1, -1, 0x0102, Int16.min])
        try ecrivain.fermer()

        XCTAssertEqual(Array(try donnees(url)), [0x01, 0x00, 0xFF, 0xFF, 0x02, 0x01, 0x00, 0x80])
    }

    func testUneTrameIncompleteEstRefusee() throws {
        let ecrivain = try EcrivainWAV(url: dossier.appendingPathComponent("x.wav"), canaux: 2,
                                       frequence: 48_000, tramesParTranche: 4)

        XCTAssertThrowsError(try ecrivain.ajouter([1, 2, 3]))
    }

    // MARK: - Arrêt brutal et reprise

    func testUnArretBrutalLaisseUnFichierLisibleJusquALaDerniereTranche() throws {
        let url = dossier.appendingPathComponent("brutal.wav")
        var ecrivain: EcrivainWAV? = try EcrivainWAV(url: url, canaux: 2, frequence: 48_000,
                                                     tramesParTranche: 2)
        try ecrivain?.ajouter(echantillons(trames: 5))
        // Sans `fermer` : la trame en attente est perdue, les deux tranches restent.
        let copie = dossier.appendingPathComponent("copie-avant-fermeture.wav")
        try FileManager.default.copyItem(at: url, to: copie)
        ecrivain = nil

        let entete = try EcrivainWAV.lireEntete(copie)
        XCTAssertEqual(entete.trames, 4)
        XCTAssertEqual(try donnees(copie).count, Int(entete.octetsDonnees))
    }

    func testReparerRecaleLEnteteSurLeDisqueEtRetireLaTrameIncomplete() throws {
        let url = dossier.appendingPathComponent("interrompu.wav")
        let ecrivain = try EcrivainWAV(url: url, canaux: 2, frequence: 48_000, tramesParTranche: 2)
        try ecrivain.ajouter(echantillons(trames: 4))
        try ecrivain.fermer()
        // Plantage entre l'écriture d'une tranche et la mise à jour de l'en-tête :
        // trois trames et demie de plus sur le disque, l'en-tête ne les compte pas.
        let fichier = try FileHandle(forWritingTo: url)
        try fichier.seekToEnd()
        try fichier.write(contentsOf: Data(repeating: 0x7F, count: 3 * 4 + 2))
        try fichier.close()
        XCTAssertEqual(try EcrivainWAV.lireEntete(url).trames, 4)

        let trames = try EcrivainWAV.reparer(url)

        XCTAssertEqual(trames, 7)
        XCTAssertEqual(try EcrivainWAV.lireEntete(url).trames, 7)
        XCTAssertEqual(try donnees(url).count, 7 * 4)
        XCTAssertEqual(try EcrivainWAV.reparer(url), 7, "réparer deux fois ne change rien")
    }

    func testReparerRefuseCeQuiNEstPasUnWAV() throws {
        let url = dossier.appendingPathComponent("texte.wav")
        try Data("pas un wav du tout, juste du texte assez long".utf8).write(to: url)

        XCTAssertThrowsError(try EcrivainWAV.reparer(url))
    }

    // MARK: - C5 : jamais par-dessus un fichier existant

    func testUnFichierExistantNEstJamaisEcrase() throws {
        let url = dossier.appendingPathComponent("visio.wav")
        try Data("enregistrement précieux".utf8).write(to: url)
        XCTAssertThrowsError(try EcrivainWAV(url: url, canaux: 2, frequence: 48_000, tramesParTranche: 4)) { erreur in
            XCTAssertTrue("\(erreur)".contains("existe déjà"), "\(erreur)")
        }
        XCTAssertEqual(try Data(contentsOf: url), Data("enregistrement précieux".utf8), "rien n'est écrasé")
    }

    func testUnFichierNeufEstCreeAvecSonEntete() throws {
        let url = dossier.appendingPathComponent("neuf.wav")
        _ = try EcrivainWAV(url: url, canaux: 2, frequence: 48_000, tramesParTranche: 4)
        XCTAssertEqual(try EcrivainWAV.lireEntete(url), EnteteWAV(canaux: 2, frequence: 48_000, octetsDonnees: 0))
    }

    // MARK: - C6 : la première erreur d'écriture arrête tout de suite

    private struct Panne: Error {}

    func testLaPremiereErreurSArreteEtNeSeSignaleQuUneFois() {
        var garde = GardeEcriture()
        var ecritures = 0
        XCTAssertNil(garde.ecrire { ecritures += 1 })
        XCTAssertTrue(garde.ecrire { ecritures += 1; throw Panne() } is Panne, "signalée tout de suite")
        XCTAssertNil(garde.ecrire { ecritures += 1 }, "une seule fois")
        XCTAssertNil(garde.ecrire { ecritures += 1 })
        XCTAssertEqual(ecritures, 2, "plus rien n'est écrit après l'erreur")
        XCTAssertTrue(garde.erreur is Panne, "l'arrêt la rend")
    }

    func testApresUneErreurLeFichierEcritJusqueLaSeRepare() throws {
        let url = dossier.appendingPathComponent("interrompu.wav")
        let ecrivain = try EcrivainWAV(url: url, canaux: 2, frequence: 16_000, tramesParTranche: 2)
        var garde = GardeEcriture()
        XCTAssertNil(garde.ecrire { try ecrivain.ajouter(echantillons(trames: 4)) })
        XCTAssertNotNil(garde.ecrire { throw Panne() })
        XCTAssertNil(garde.ecrire { try ecrivain.ajouter(echantillons(trames: 4, depart: 9)) })
        XCTAssertEqual(try EcrivainWAV.reparer(url), 4, "les quatre trames d'avant la panne, rien après")
    }
}

