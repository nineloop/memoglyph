import CoreAudio
import Foundation

public struct ErreurCoreAudio: Error, CustomStringConvertible {
    public let quoi: String
    public let statut: OSStatus
    public var description: String { "\(quoi) : erreur Core Audio \(statut)" }
}

func verifier(_ statut: OSStatus, _ quoi: @autoclosure () -> String) throws {
    guard statut == noErr else { throw ErreurCoreAudio(quoi: quoi(), statut: statut) }
}

func adresse(_ selecteur: AudioObjectPropertySelector,
             _ portee: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selecteur, mScope: portee, mElement: kAudioObjectPropertyElementMain)
}

func lireValeur<T: BitwiseCopyable>(_ objet: AudioObjectID, _ selecteur: AudioObjectPropertySelector, _ defaut: T,
                   portee: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> T {
    var a = adresse(selecteur, portee)
    var taille = UInt32(MemoryLayout<T>.size)
    var valeur = defaut
    let statut = withUnsafeMutableBytes(of: &valeur) {
        AudioObjectGetPropertyData(objet, &a, 0, nil, &taille, $0.baseAddress!)
    }
    try verifier(statut, "lecture de la propriété \(selecteur) de l'objet \(objet)")
    return valeur
}

func lireTableau<T: BitwiseCopyable>(_ objet: AudioObjectID, _ selecteur: AudioObjectPropertySelector, _ defaut: T) throws -> [T] {
    var a = adresse(selecteur)
    var taille: UInt32 = 0
    try verifier(AudioObjectGetPropertyDataSize(objet, &a, 0, nil, &taille),
                 "taille de la propriété \(selecteur)")
    var tableau = [T](repeating: defaut, count: Int(taille) / MemoryLayout<T>.stride)
    guard !tableau.isEmpty else { return [] }
    let statut = tableau.withUnsafeMutableBytes {
        AudioObjectGetPropertyData(objet, &a, 0, nil, &taille, $0.baseAddress!)
    }
    try verifier(statut, "lecture de la propriété \(selecteur)")
    return Array(tableau.prefix(Int(taille) / MemoryLayout<T>.stride))
}

func lireChaine(_ objet: AudioObjectID, _ selecteur: AudioObjectPropertySelector) throws -> String {
    var a = adresse(selecteur)
    var taille = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    var valeur: Unmanaged<CFString>?
    try verifier(AudioObjectGetPropertyData(objet, &a, 0, nil, &taille, &valeur),
                 "lecture de la chaîne \(selecteur) de l'objet \(objet)")
    return valeur.map { $0.takeRetainedValue() as String } ?? ""
}

let objetSysteme = AudioObjectID(kAudioObjectSystemObject)
