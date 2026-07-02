import SwiftUI

// SPATAIL iOS Viewer — the on-device twin of webxr/. AR canvas + a HUD that mirrors
// the web viewer's controls: a prompt to generate from the brain, an Identify/Hitbox
// overlay, object-local Controls, and Live camera perception.

@main
struct SpatailViewerApp: App {
    var body: some Scene { WindowGroup { ViewerScreen() } }
}

struct ViewerScreen: View {
    @StateObject private var model = ViewerModel()
    @State private var showSettings = false
    @FocusState private var promptFocused: Bool

    var body: some View {
        ZStack {
            ViewerARView(model: model).ignoresSafeArea()

            // --- identify hitboxes (projected) -----------------------------------
            if model.identifyOn {
                ForEach(model.identified) { item in
                    ZStack(alignment: .topLeading) {
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(item.color, lineWidth: 2)
                            .frame(width: item.rect.width, height: item.rect.height)
                        label("\(item.title) · \(item.intent)\(item.placeholder ? " · generating" : "")", item.color)
                            .offset(y: -20)
                        if model.controlsOn && model.selectedId == item.id {
                            controlPanel(for: item).offset(y: item.rect.height + 4)
                        }
                    }
                    .position(x: item.rect.midX, y: item.rect.midY)
                    .allowsHitTesting(model.controlsOn)
                }
            }

            // --- live detections (camera-frame boxes) ----------------------------
            if model.liveOn {
                GeometryReader { geo in
                    ForEach(model.detections) { d in
                        let r = rect(d.bbox, in: geo.size)
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color(red: 0.10, green: 0.76, blue: 0.65), lineWidth: 2)
                            .overlay(alignment: .topLeading) {
                                label("\(d.label) \(Int(d.confidence * 100))%", Color(red: 0.10, green: 0.76, blue: 0.65))
                                    .offset(y: -20)
                            }
                            .frame(width: r.width, height: r.height)
                            .position(x: r.midX, y: r.midY)
                    }
                }.allowsHitTesting(false)
            }

            VStack {
                topBar
                Spacer()
                bottomBar
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings) { settings }
        .onAppear {
            if model.brainURL.isEmpty || model.detectorURL.isEmpty { showSettings = true }
        }
    }

    // MARK: - bars
    private var topBar: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Text("SPAT").bold() + Text("AI").bold().foregroundColor(.purple) + Text("L").bold()
                TextField("ask the brain… e.g. how a pendulum works", text: $model.prompt)
                    .textFieldStyle(.roundedBorder).focused($promptFocused)
                    .submitLabel(.go).onSubmit { generate() }
                Button(action: generate) { Text(model.generating ? "…" : "✦ Generate") }
                    .buttonStyle(.borderedProminent).disabled(model.generating)
                Button { showSettings = true } label: { Image(systemName: "gearshape") }
            }
            HStack(spacing: 8) {
                toggle("⊞ Identify", $model.identifyOn)
                toggle("⚙ Controls", $model.controlsOn)
                toggle("◎ Live cam", $model.liveOn)
                Button("⟲ Replace") { model.onReplaceAnchor?() }.buttonStyle(.bordered)
                Spacer()
            }
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var bottomBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let p = model.progress {
                ProgressView(value: Double(p.index), total: Double(max(p.total, 1))) {
                    Text("Meshy: \(p.stage) (\(p.index)/\(p.total)) · \(p.percent)%").font(.caption)
                }.tint(.purple)
            }
            if !model.title.isEmpty {
                Text(model.title).font(.headline) + Text("  \(model.subtitle)").font(.subheadline).foregroundColor(.secondary)
            }
            Text(model.status).font(.caption).foregroundColor(.secondary)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    // MARK: - object-local control panel (MF p29/p35)
    private func controlPanel(for item: IdentifiedItem) -> some View {
        HStack(spacing: 4) {
            ForEach(item.affordances + ["reset"], id: \.self) { verb in
                Button(verb) { model.onBehavior?(item.id, verb) }
                    .font(.caption2).padding(.horizontal, 7).padding(.vertical, 4)
                    .background(.thinMaterial, in: Capsule())
            }
        }
        .padding(5).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .fixedSize()
    }

    // MARK: - settings
    private var settings: some View {
        NavigationStack {
            Form {
                Section("PC brain (job_server)") {
                    TextField("http://<tailscale-host>:8788", text: $model.brainURL)
                        .keyboardType(.URL).autocorrectionDisabled().textInputAutocapitalization(.never)
                }
                Section("Perception (detector_server)") {
                    TextField("http://<tailscale-host>:8766", text: $model.detectorURL)
                        .keyboardType(.URL).autocorrectionDisabled().textInputAutocapitalization(.never)
                }
                Section {
                    Text("Set these to your PC's Tailscale or LAN address. The viewer talks to the same servers the web viewer uses.")
                        .font(.caption).foregroundColor(.secondary)
                }
            }
            .navigationTitle("Server")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") {
                ViewerConfig.brainURL = model.brainURL
                ViewerConfig.detectorURL = model.detectorURL
                showSettings = false
            } } }
        }
    }

    // MARK: - bits
    private func generate() { promptFocused = false; model.onGenerate?(model.prompt) }
    private func toggle(_ title: String, _ b: Binding<Bool>) -> some View {
        Button(title) { b.wrappedValue.toggle() }
            .buttonStyle(.bordered).tint(b.wrappedValue ? .purple : .gray)
    }
    private func label(_ text: String, _ color: Color) -> some View {
        Text(text).font(.caption2).lineLimit(1).padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.85), in: Capsule()).foregroundColor(.white).fixedSize()
    }
    private func rect(_ bbox: [Double], in size: CGSize) -> CGRect {
        guard bbox.count == 4 else { return .zero }
        return CGRect(x: bbox[0] * size.width, y: bbox[1] * size.height,
                      width: bbox[2] * size.width, height: bbox[3] * size.height)
    }
}
