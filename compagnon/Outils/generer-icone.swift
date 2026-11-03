// Icône du compagnon : le symbole Memoglyph clair sur un carré arrondi au format macOS, fond en dégradé sombre
// (#1B4A4D vers #0E2A2E, la teinte du dock), symbole à environ 72 % de la largeur. Produit un `.iconset` que
// `iconutil` convertit en `.icns` (voir la cible `companion` du Makefile). Rien n'est téléchargé et aucune image
// n'est versionnée : le dessin vient du SVG de la marque, avec les seuls outils de macOS.
//
//   swift compagnon/Outils/generer-icone.swift <symbole.svg> <dossier.iconset> [apercu.png]
import AppKit

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    fputs("usage : generer-icone.swift <symbole.svg> <dossier.iconset> [apercu.png]\n", stderr)
    exit(2)
}
guard let symbole = NSImage(contentsOf: URL(fileURLWithPath: arguments[1])), symbole.size.width > 0 else {
    fputs("symbole illisible : \(arguments[1])\n", stderr)
    exit(1)
}

func couleur(_ hex: Int) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

/// Une icône carrée de `pixels` de côté. Grille macOS : le corps occupe 824 / 1024 du carré, coins arrondis.
func dessiner(_ pixels: Int) -> Data {
    let representation = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
        hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    representation.size = NSSize(width: pixels, height: pixels)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: representation)
    defer { NSGraphicsContext.restoreGraphicsState() }

    let cote = CGFloat(pixels)
    let corps = cote * 824 / 1024
    let marge = (cote - corps) / 2
    let zone = NSRect(x: marge, y: marge, width: corps, height: corps)
    let forme = NSBezierPath(roundedRect: zone, xRadius: corps * 0.2237, yRadius: corps * 0.2237)
    NSGradient(starting: couleur(0x1B4A4D), ending: couleur(0x0E2A2E))!.draw(in: forme, angle: -90)

    let largeur = corps * 0.72
    let hauteur = largeur * symbole.size.height / symbole.size.width
    symbole.draw(in: NSRect(x: zone.midX - largeur / 2, y: zone.midY - hauteur / 2, width: largeur, height: hauteur),
                 from: .zero, operation: .sourceOver, fraction: 1)
    return representation.representation(using: .png, properties: [:])!
}

let dossier = URL(fileURLWithPath: arguments[2], isDirectory: true)
try FileManager.default.createDirectory(at: dossier, withIntermediateDirectories: true)
// Les tailles d'un `.iconset` : nom → côté en pixels.
let tailles: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]
for (nom, pixels) in tailles {
    try dessiner(pixels).write(to: dossier.appendingPathComponent(nom + ".png"))
}
if arguments.count >= 4 { try dessiner(1024).write(to: URL(fileURLWithPath: arguments[3])) }
print("icône : \(tailles.count) tailles dans \(dossier.path)")
