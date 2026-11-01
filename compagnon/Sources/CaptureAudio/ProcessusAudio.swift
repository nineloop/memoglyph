import CaptureCore
import CoreAudio
import Darwin
import Foundation

/// Les processus qui ont ouvert l'audio, d'après Core Audio (macOS 14.2 et plus).
public enum ProcessusAudio {
    public static func lister() throws -> [ProcessusDecrit] {
        let objets = try lireTableau(objetSysteme, kAudioHardwarePropertyProcessObjectList, AudioObjectID(0))
        return objets.compactMap { objet in
            guard let pid = try? lireValeur(objet, kAudioProcessPropertyPID, pid_t(0)) else { return nil }
            let paquet = (try? lireChaine(objet, kAudioProcessPropertyBundleID)) ?? ""
            let joue = ((try? lireValeur(objet, kAudioProcessPropertyIsRunningOutput, UInt32(0))) ?? 0) != 0
            let micro = ((try? lireValeur(objet, kAudioProcessPropertyIsRunningInput, UInt32(0))) ?? 0) != 0
            return ProcessusDecrit(objet: objet, pid: pid, nom: nomExecutable(pid), identifiant: paquet, joue: joue,
                                   prendMicro: micro)
        }
        .sorted { ($0.nom.lowercased(), $0.pid) < ($1.nom.lowercased(), $1.pid) }
    }

    static func nomExecutable(_ pid: pid_t) -> String {
        var chemin = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        guard proc_pidpath(pid, &chemin, UInt32(chemin.count)) > 0 else { return "?" }
        return URL(fileURLWithPath: String(cString: chemin)).lastPathComponent
    }
}
