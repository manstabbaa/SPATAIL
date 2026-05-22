// RoomScanView.swift
//
// First-launch surface. The user holds the phone up and sweeps the
// room; a minimal HUD shows coverage; a single button unlocks when
// coverage crosses the threshold.
//
// Design rules:
//   - One ring (coverage), one reticle, one chip (lidar / heuristic).
//   - No demo selector visible. No menus. The room IS the entry point.
//   - When coverage ≥ 0.75 the Continue button appears, slides up.

import SwiftUI
import ARKit
import RealityKit

struct RoomScanView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var scanner = RoomScannerService()
    @State private var navigateToDemoSelector = false

    private let unlockThreshold: Float = 0.75

    var body: some View {
        ZStack {
            // The AR session view; takes the whole screen.
            ScannerARView(scanner: scanner)
                .ignoresSafeArea()

            // Centered reticle + ring around it for the coverage value.
            VStack {
                Spacer()
                CoverageReticle(progress: scanner.coverage)
                    .frame(width: 180, height: 180)
                Spacer().frame(height: 8)
                Text(scanner.coverage < unlockThreshold
                     ? "Keep sweeping the room"
                     : "Room captured — continue when ready")
                    .font(.callout).fontWeight(.semibold)
                    .foregroundColor(.white)
                    .shadow(color: .black.opacity(0.6), radius: 6)
                Spacer()
            }

            // HUD: top-left chip showing tier, top-right area-by-kind tally.
            VStack {
                HStack {
                    TierChip(lidar: scanner.lidarAvailable,
                             state: scanner.sessionState)
                    Spacer()
                    AreaTally(areaByKind: scanner.areaByKind)
                }
                Spacer()
                // Continue is the only persistent control.
                if scanner.coverage >= unlockThreshold {
                    Button(action: continueTapped) {
                        HStack(spacing: 10) {
                            Image(systemName: "checkmark.circle.fill")
                            Text("Continue")
                                .font(.headline)
                        }
                        .padding(.horizontal, 24).padding(.vertical, 14)
                        .background(
                            LinearGradient(
                                colors: [.spatailAccent, .spatailAccent2],
                                startPoint: .leading, endPoint: .trailing),
                        )
                        .foregroundColor(.white)
                        .clipShape(Capsule())
                        .shadow(color: .black.opacity(0.4), radius: 12)
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .padding(.bottom, 36)
                }
            }
            .padding(14)
            .animation(.easeInOut(duration: 0.25), value: scanner.coverage >= unlockThreshold)
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        .navigationDestination(isPresented: $navigateToDemoSelector) {
            DemoSelectorView()
        }
    }

    private func continueTapped() {
        do {
            _ = try scanner.finalize()
            navigateToDemoSelector = true
        } catch {
            // SPATAIL_NEEDS_MAC_BUILD_VERIFY: error path — for now we
            // just hop to the selector; in a future pass we surface
            // the error with a retry.
            navigateToDemoSelector = true
        }
    }
}

// MARK: - Coverage ring

private struct CoverageReticle: View {
    let progress: Float
    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 4)
            Circle()
                .trim(from: 0, to: CGFloat(max(0, min(1, progress))))
                .stroke(
                    LinearGradient(colors: [.spatailAccent, .spatailAccent2],
                                   startPoint: .top, endPoint: .bottom),
                    style: StrokeStyle(lineWidth: 6, lineCap: .round),
                )
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.2), value: progress)
            VStack(spacing: 2) {
                Text("\(Int(progress * 100))%")
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .foregroundColor(.white)
                Text("captured")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.white.opacity(0.65))
            }
            // Tiny reticle in the dead center so the user knows where to point.
            Rectangle()
                .fill(Color.spatailAccent)
                .frame(width: 2, height: 14)
            Rectangle()
                .fill(Color.spatailAccent)
                .frame(width: 14, height: 2)
        }
    }
}

private struct TierChip: View {
    let lidar: Bool
    let state: String
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: lidar ? "scanner.fill" : "rectangle.dashed")
                .imageScale(.small)
            Text(lidar ? "LiDAR" : "Plane heuristic")
                .font(.caption2).fontWeight(.semibold)
        }
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(Color.black.opacity(0.45))
        .foregroundColor(.white)
        .clipShape(Capsule())
    }
}

private struct AreaTally: View {
    let areaByKind: [SurfaceKind: Float]
    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            ForEach(SurfaceKind.allCases, id: \.self) { kind in
                let area = areaByKind[kind] ?? 0
                if area >= 0.05 {
                    HStack(spacing: 6) {
                        Text(kind.rawValue)
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundColor(.white.opacity(0.85))
                        Text(String(format: "%.1f m²", area))
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundColor(.white)
                    }
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color.black.opacity(0.45))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - ARSession view

private struct ScannerARView: UIViewRepresentable {
    let scanner: RoomScannerService

    func makeUIView(context: Context) -> ARView {
        // SPATAIL_NEEDS_MAC_BUILD_VERIFY: ARView(frame:cameraMode:automaticallyConfigureSession:)
        // signature — cameraMode defaults to .ar; we pass it explicitly so
        // the call site reads honestly.
        let arView = ARView(frame: .zero, cameraMode: .ar,
                            automaticallyConfigureSession: false)
        scanner.attach(session: arView.session)
        arView.session.run(scanner.configuration())
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        // No-op: ARView observes its session directly.
    }
}
