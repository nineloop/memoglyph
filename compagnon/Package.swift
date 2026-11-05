// swift-tools-version:5.10
// Compagnon de capture de Memoglyph (docs/compagnon-capture.md) : un outil en ligne de
// commande qui enregistre le son d'une application et le micro, et l'application de barre de menus.
// Aucune dépendance externe : Foundation, Core Audio et AVFoundation seulement.
import PackageDescription

let package = Package(
    name: "MemoglyphCompagnon",
    // C1 : les process taps de Core Audio existent depuis macOS 14.2 ; 14.4 corrige
    // leurs autorisations.
    platforms: [.macOS("14.4")],
    products: [
        .executable(name: "memoglyph-capture", targets: ["memoglyph-capture"]),
        .executable(name: "memoglyph-compagnon", targets: ["memoglyph-compagnon"]),
    ],
    targets: [
        // Logique pure, sans Core Audio : testable partout.
        .target(name: "CaptureCore"),
        // Process tap, appareil agrégé, micro, autorisations.
        .target(name: "CaptureAudio", dependencies: ["CaptureCore"]),
        .executableTarget(
            name: "memoglyph-capture",
            dependencies: ["CaptureCore", "CaptureAudio"],
            exclude: ["Info.plist"],
            // macOS lit les raisons d'usage (micro, son du système) dans un
            // Info.plist ; un outil en ligne de commande l'embarque dans son binaire.
            linkerSettings: [.unsafeFlags([
                "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                "-Xlinker", "Sources/memoglyph-capture/Info.plist",
            ])]
        ),
        // Application de barre de menus (AppKit), sans icône dans le Dock (LSUIElement).
        .executableTarget(
            name: "memoglyph-compagnon",
            dependencies: ["CaptureCore", "CaptureAudio"],
            exclude: ["Info.plist"],
            linkerSettings: [.unsafeFlags([
                "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                "-Xlinker", "Sources/memoglyph-compagnon/Info.plist",
            ])]
        ),
        .testTarget(name: "CaptureCoreTests", dependencies: ["CaptureCore"]),
    ]
)
