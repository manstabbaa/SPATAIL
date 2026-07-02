import SwiftUI

// The app's single entry: the LIVE CAMERA canvas with a prompt box at the bottom.
// No menu of modes. Type a prompt → it builds into your space (lazily creating a
// project). The stacked-squares button opens saved Projects; pick one to reopen and
// keep evolving it. One ARView for the whole app (CanvasView), swapped by .id.

struct HomeView: View {
    @StateObject private var store = ProjectStore()
    @State private var activeMeta: ProjectMeta?      // nil = a fresh scratch canvas
    @State private var scratchNonce = 0
    @State private var showProjects = false
    @State private var showFactory = false
    @State private var serverURL = GenerativeClient.baseURL

    var body: some View {
        content
            .sheet(isPresented: $showProjects) { projectsSheet }
            .fullScreenCover(isPresented: $showFactory) { factoryCover }
    }

    @ViewBuilder private var factoryCover: some View {
        #if os(iOS)
        FactoryBrowseView()
        #else
        Color.black.overlay(Text("Asset Factory — available on iPhone").foregroundStyle(.white))
        #endif
    }

    @ViewBuilder private var content: some View {
        #if os(iOS)
        CanvasView(store: store, meta: activeMeta,
                   onOpenProjects: { store.reload(); showProjects = true })
            .id(activeMeta?.id ?? "scratch-\(scratchNonce)")    // swap session on project change
        #else
        Color.black.overlay(Text("SPATAIL — available on iPhone").foregroundStyle(.white))
        #endif
    }

    private var projectsSheet: some View {
        NavigationStack {
            List {
                Section("Server") {
                    TextField("http://your-pc.tailnet:8787", text: $serverURL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .onChange(of: serverURL) { _, v in GenerativeClient.baseURL = v }
                }
                Section {
                    Button { newScratch() } label: { Label("New experience", systemImage: "plus") }
                    Button {
                        showProjects = false
                        // present the cover after the sheet finishes dismissing
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { showFactory = true }
                    } label: {
                        Label("Asset Factory — test normalized assets", systemImage: "shippingbox")
                    }
                }
                if store.projects.isEmpty {
                    Section { Text("No projects yet — type a prompt to create one.")
                        .foregroundStyle(.secondary) }
                } else {
                    Section("Projects") {
                        ForEach(store.projects) { p in
                            Button { open(p) } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(p.title.isEmpty ? p.firstPrompt : p.title).bold()
                                    Text(p.updatedAt, style: .date)
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                            }.buttonStyle(.plain)
                        }
                        .onDelete { idx in idx.map { store.projects[$0].id }.forEach(store.delete) }
                    }
                }
            }
            .navigationTitle("Projects")
            .toolbar { ToolbarItem(placement: .confirmationAction) {
                Button("Done") { showProjects = false } } }
        }
    }

    private func newScratch() { activeMeta = nil; scratchNonce += 1; showProjects = false }
    private func open(_ p: ProjectMeta) { activeMeta = p; showProjects = false }
}
