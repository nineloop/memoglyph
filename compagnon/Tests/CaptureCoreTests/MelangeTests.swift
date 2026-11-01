import CaptureCore
import XCTest

final class MelangeTests: XCTestCase {
    func testLeMicroVaAGaucheEtLeSystemeADroite() {
        let sortie = Melange.entrelacer(micro: [0.5, -0.5], systeme: [0.25, 1])

        XCTAssertEqual(sortie, [16384, 8192, -16384, 32767])
    }

    func testSansMicroLaGaucheResteMuette() {
        let sortie = Melange.entrelacer(micro: nil, systeme: [0.5, 0.5, 0.5])

        XCTAssertEqual(sortie.count, 6)
        XCTAssertEqual(stride(from: 0, to: 6, by: 2).map { sortie[$0] }, [0, 0, 0])
        XCTAssertEqual(stride(from: 1, to: 6, by: 2).map { sortie[$0] }, [16384, 16384, 16384])
    }

    func testUnMicroPlusCourtEstCompleteParDuSilence() {
        let sortie = Melange.entrelacer(micro: [1], systeme: [0, 0])

        XCTAssertEqual(sortie, [32767, 0, 0, 0])
    }

    func testLaConversionSatureAuLieuDeReplier() {
        XCTAssertEqual(Melange.versInt16(1.7), Int16.max)
        XCTAssertEqual(Melange.versInt16(-3), -Int16.max)
        XCTAssertEqual(Melange.versInt16(.nan), 0)
        XCTAssertEqual(Melange.versInt16(.infinity), 0)
        XCTAssertEqual(Melange.versInt16(0), 0)
    }

    func testPlusieursCanauxDeviennentUnMonoParMoyenne() {
        XCTAssertEqual(Melange.mono([1, 0, 0.5, 0.5, -1, 1], canaux: 2), [0.5, 0.5, 0])
        XCTAssertEqual(Melange.mono([0.1, 0.2], canaux: 1), [0.1, 0.2])
    }

    func testLaCreteRepereUnCanalMuet() {
        XCTAssertEqual(Melange.crete([0, 0, 0]), 0)
        XCTAssertEqual(Melange.crete([0.1, -0.6, 0.3]), 0.6, accuracy: 1e-6)
    }
}
