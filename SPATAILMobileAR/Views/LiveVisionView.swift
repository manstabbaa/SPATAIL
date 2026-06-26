// LiveVisionView.swift
//
// The "point your phone, the PC tells you what it is" screen — the joint
// activity that makes camera input real. Shows the live rear camera, streams
// frames to the PC vision engine, and overlays the VLM's identification +
// bounding box on top. Pairs with the engine's browser debug view (watch both
// over Parsec to see the full input→output loop).

import SwiftUI
import AVFoundation

struct LiveVisionView: View {
    @StateObject private var model = LiveVisionModel()
    @State private var editingHost = false

    var body: some View {
        ZStack {
            CameraPreview(session: model.streamer.session)
                .ignoresSafeArea()

            // Bounding box for the primary detection, drawn in view space.
            GeometryReader { geo in
                if let box = model.lastIdentification?.detections.first?.box, box.count == 4 {
                    let rect = CGRect(x: box[0] * geo.size.width,
                                      y: box[1] * geo.size.height,
                                      width: box[2] * geo.size.width,
                                      height: box[3] * geo.size.height)
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.spatailAccent, lineWidth: 3)
                        .frame(width: rect.width, height: rect.height)
                        .position(x: rect.midX, y: rect.midY)
                }
            }
            .ignoresSafeArea()

            VStack {
                statusBar
                Spacer()
                identificationCard
            }
            .padding(16)
        }
        .navigationTitle("Live Vision")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.startPreview() }
        .onDisappear { model.stopAll() }
    }

    // MARK: top status

    private var statusBar: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            Text(statusText)
                .font(.caption).fontWeight(.semibold)
                .foregroundColor(.white)
            Spacer()
            Button(model.state == .streaming ? "Disconnect" : "Connect") {
                if model.state == .streaming { model.disconnect() } else { model.connect() }
            }
            .font(.caption).fontWeight(.bold)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color.spatailAccent)
            .foregroundColor(.white)
            .clipShape(Capsule())
        }
        .padding(10)
        .background(.black.opacity(0.45))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(alignment: .bottomLeading) { hostField }
    }

    private var hostField: some View {
        HStack(spacing: 6) {
            Image(systemName: "desktopcomputer").imageScale(.small)
            TextField("pc-ip:8798", text: $model.serverHost)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .font(.system(.caption2, design: .monospaced))
                .frame(maxWidth: 180)
                .disabled(model.state == .streaming)
        }
        .foregroundColor(.white.opacity(0.85))
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(.black.opacity(0.5))
        .clipShape(Capsule())
        .offset(y: 42)
    }

    // MARK: bottom identification card

    private var identificationCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let id = model.lastIdentification {
                HStack(alignment: .firstTextBaseline) {
                    Text(id.primary ?? "—")
                        .font(.title2).bold()
                        .foregroundColor(.white)
                    Spacer()
                    if let c = id.detections.first?.confidence {
                        Text("\(Int(c * 100))%")
                            .font(.headline)
                            .foregroundColor(.spatailAccent)
                    }
                }
                HStack(spacing: 12) {
                    if let ms = id.latencyMs { Label("\(ms) ms", systemImage: "clock") }
                    if let m = id.model { Label(m, systemImage: "cpu") }
                    Label("\(model.framesSent) frames", systemImage: "arrow.up")
                }
                .font(.caption2)
                .foregroundColor(.spatailTextDim)
                if id.detections.count > 1 {
                    Text(id.detections.dropFirst().map(\.label).joined(separator: " · "))
                        .font(.caption2)
                        .foregroundColor(.white.opacity(0.6))
                        .lineLimit(1)
                }
            } else {
                Text(model.state == .streaming
                     ? "Point at an object — waiting for the engine…"
                     : "Connect to your PC vision engine, then point the camera.")
                    .font(.callout)
                    .foregroundColor(.white.opacity(0.85))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var statusColor: Color {
        switch model.state {
        case .streaming: return .green
        case .connecting: return .yellow
        case .failed: return .red
        case .idle: return .gray
        }
    }

    private var statusText: String {
        switch model.state {
        case .idle: return "Camera ready"
        case .connecting: return "Connecting…"
        case .streaming: return "Streaming to PC"
        case .failed(let m): return "Error: \(m)"
        }
    }
}

// MARK: - Camera preview (AVCaptureVideoPreviewLayer)

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.videoPreviewLayer.session = session
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer {
            layer as! AVCaptureVideoPreviewLayer
        }
    }
}
