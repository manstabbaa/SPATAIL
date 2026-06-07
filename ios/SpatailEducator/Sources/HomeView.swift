import SwiftUI

// The app's single entry point. Replaces the old menu of modes (Generate / Lesson /
// Representation / Object / Modular / Live / Educational) with ONE prompt that
// creates a project, plus the saved-projects folder you reopen and keep prompting.

struct HomeView: View {
    @StateObject private var store = ProjectStore()
    @State private var newPrompt = ""
    @State private var serverURL = GenerativeClient.baseURL
    @State private var openTarget: ProjectMeta?
    @State private var openInitialPrompt: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Describe an experience and build it in your space. Open a project to keep evolving it by prompting.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Section("New experience") {
                    TextField("e.g. how a V8 engine works on my table", text: $newPrompt, axis: .vertical)
                        .lineLimit(1...3)
                    DisclosureGroup("Server") {
                        TextField("http://your-pc.tailnet:8787", text: $serverURL)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .onChange(of: serverURL) { _, v in GenerativeClient.baseURL = v }
                    }.font(.caption)
                    Button(action: startNew) {
                        Label("Create", systemImage: "sparkles").bold()
                    }
                    .disabled(newPrompt.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if !store.projects.isEmpty {
                    Section("Projects") {
                        ForEach(store.projects) { p in
                            Button { openInitialPrompt = nil; openTarget = p } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(p.title.isEmpty ? p.firstPrompt : p.title).bold()
                                        Text(p.updatedAt, style: .date)
                                            .font(.caption2).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: "chevron.right").foregroundStyle(.secondary)
                                }
                            }.buttonStyle(.plain)
                        }
                        .onDelete { idx in idx.map { store.projects[$0].id }.forEach(store.delete) }
                    }
                }
            }
            .navigationTitle("SPATAIL")
            .navigationDestination(item: $openTarget) { meta in
                ProjectView(meta: meta, store: store, initialPrompt: openInitialPrompt)
            }
            .onAppear { store.reload() }
        }
    }

    private func startNew() {
        GenerativeClient.baseURL = serverURL
        let p = newPrompt.trimmingCharacters(in: .whitespaces)
        let meta = store.create(title: "", firstPrompt: p)
        openInitialPrompt = p
        openTarget = meta
        newPrompt = ""
    }
}
