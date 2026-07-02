import SwiftUI
#if os(iOS)
import UIKit

// Browse the AI Asset Factory's normalized assets (GET /factory/index) as a grid
// of preview thumbnails, then open one in the AR QA inspector. This is the
// on-device counterpart to the factory: "did it import, is it scaled, is it
// centered, is it broken?" — answered against the real normalized USDZ.

struct FactoryBrowseView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var assets: [FactoryAsset] = []
    @State private var status = "Loading normalized assets…"
    @State private var loading = true
    @State private var selected: FactoryAsset?
    @State private var serverURL = GenerativeClient.baseURL
    private let client = FactoryClient()

    private let cols = [GridItem(.adaptive(minimum: 150), spacing: 12)]

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    ProgressView(status)
                } else if assets.isEmpty {
                    empty
                } else {
                    grid
                }
            }
            .navigationTitle("Asset Factory")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }
                }
            }
            .fullScreenCover(item: $selected) { a in FactoryQAView(asset: a) }
        }
        .task { await reload() }
    }

    private var grid: some View {
        ScrollView {
            LazyVGrid(columns: cols, spacing: 12) {
                ForEach(assets) { a in
                    Button { selected = a } label: { FactoryCard(asset: a, client: client) }
                        .buttonStyle(.plain)
                        .disabled(!a.hasUsdz)
                        .opacity(a.hasUsdz ? 1 : 0.5)
                }
            }
            .padding()
        }
        .refreshable { await reload() }
    }

    private var empty: some View {
        VStack(spacing: 12) {
            Image(systemName: "shippingbox").font(.largeTitle).foregroundStyle(.secondary)
            Text("No normalized assets yet").font(.headline)
            Text(status).font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).padding(.horizontal)
            Text("On the PC: process assets with USDZ, e.g.\npython asset_factory/worker_manager.py --input assets_raw --output assets_processed --export-usdz")
                .font(.caption2).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).padding(.horizontal)
            DisclosureGroup("Server") {
                TextField("http://your-pc.tailnet:8787", text: $serverURL)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled().textInputAutocapitalization(.never)
                    .onChange(of: serverURL) { _, v in GenerativeClient.baseURL = v }
                Button("Retry") { Task { await reload() } }.buttonStyle(.borderedProminent)
            }.font(.caption).padding(.horizontal)
        }.padding()
    }

    private func reload() async {
        loading = true; status = "Loading normalized assets…"
        do {
            assets = try await client.fetchIndex()
            if assets.isEmpty { status = "The factory hasn't produced any assets yet." }
        } catch {
            assets = []; status = error.localizedDescription
        }
        loading = false
    }
}

private struct FactoryCard: View {
    let asset: FactoryAsset
    let client: FactoryClient
    @State private var image: UIImage?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                RoundedRectangle(cornerRadius: 10).fill(Color.gray.opacity(0.15))
                if let img = image {
                    Image(uiImage: img).resizable().scaledToFit().padding(6)
                } else {
                    ProgressView()
                }
            }
            .frame(height: 130)
            .clipShape(RoundedRectangle(cornerRadius: 10))

            Text(asset.title).font(.subheadline).bold().lineLimit(1)
            Text(String(format: "%.2f × %.2f × %.2f m",
                        asset.finalBounds.x, asset.finalBounds.y, asset.finalBounds.z))
                .font(.caption2).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                Label("\(asset.triangleCount)", systemImage: "triangle")
                if !asset.hasUsdz {
                    Text("no USDZ").foregroundStyle(.orange)
                }
            }
            .font(.caption2).foregroundStyle(.secondary)
        }
        .task { image = await client.previewImage(asset) }
    }
}
#endif
