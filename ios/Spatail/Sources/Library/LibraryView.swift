// LibraryView.swift — saved experiences as a glass bento grid.
//
// Presented as a sheet from RootView. The newest experience takes the full-width
// featured tile; the rest pack a two-column grid. Every card carries the living
// facts (steps, assets, anchoring stream) and one action that matters:
// RE-PLACE — decode the persisted contract and diff-apply it against the room
// as it is scanned RIGHT NOW (current surfaces + registry objects).
//
// A "Recent asks" section surfaces AppModel.askHistory (session-local) with
// one-tap re-run. Styling: design-system tokens only, glass over a sunken page —
// the restyled-legacy Library pattern.

import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var runtime: ExperienceRuntime
    @EnvironmentObject private var hub: ARSessionHub
    @EnvironmentObject private var scanner: RoomScannerService
    @EnvironmentObject private var registry: ObjectRegistry
    @ObservedObject private var store = ExperienceLibraryStore.shared
    @Environment(\.dismiss) private var dismiss

    @State private var placeProblem: String?

    private var gridColumns: [GridItem] {
        [GridItem(.flexible(), spacing: SpatailSpace.s3),
         GridItem(.flexible(), spacing: SpatailSpace.s3)]
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SpatailSpace.s5) {
                header

                if let problem = placeProblem {
                    problemBanner(problem)
                }

                if store.entries.isEmpty {
                    emptyCard
                } else {
                    bento
                }

                if !model.askHistory.isEmpty {
                    recentAsks
                }

                sceneCard
            }
            .padding(SpatailSpace.s4)
        }
        .background(SpatailColor.surfaceSunken.ignoresSafeArea())
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: SpatailSpace.s1) {
                Text("SPATAIL").spatailType(.label).foregroundStyle(SpatailColor.textMuted)
                Text("Library").spatailType(.h3).foregroundStyle(SpatailColor.textStrong)
            }
            Spacer()
            SpatailIconButton(systemName: "xmark", label: "Close", variant: .outline) {
                dismiss()
            }
        }
    }

    // MARK: Bento grid (featured + 2-column)

    private var bento: some View {
        VStack(spacing: SpatailSpace.s3) {
            if let featured = store.entries.first {
                ExperienceCard(entry: featured, featured: true,
                               onPlace: { place(featured) },
                               onDelete: { store.delete(featured.id) })
            }
            LazyVGrid(columns: gridColumns, spacing: SpatailSpace.s3) {
                ForEach(store.entries.dropFirst()) { entry in
                    ExperienceCard(entry: entry, featured: false,
                                   onPlace: { place(entry) },
                                   onDelete: { store.delete(entry.id) })
                }
            }
        }
    }

    // MARK: Empty state

    private var emptyCard: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            Text("NOTHING SAVED YET")
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)
            Text("Ask for an experience in the Lens — everything the brain builds lands here, ready to re-place in any room.")
                .spatailType(.sm)
                .foregroundStyle(SpatailColor.textBody)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(SpatailSpace.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .spatailGlass(tone: .light, radius: SpatailRadius.xl)
    }

    // MARK: Recent asks (session-local, from AppModel)

    private var recentAsks: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s3) {
            Text("RECENT ASKS")
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)
            VStack(spacing: 0) {
                ForEach(Array(model.askHistory.prefix(8).enumerated()), id: \.offset) { index, prompt in
                    HStack(spacing: SpatailSpace.s2) {
                        Text(displayPrompt(prompt))
                            .spatailType(.sm)
                            .foregroundStyle(SpatailColor.textBody)
                            .lineLimit(1)
                        Spacer()
                        SpatailIconButton(systemName: "arrow.counterclockwise",
                                          label: "Ask again", size: .sm, variant: .ghost) {
                            model.ask(prompt)
                            dismiss()
                        }
                    }
                    .padding(.vertical, SpatailSpace.s2)
                    if index < min(model.askHistory.count, 8) - 1 {
                        Divider().overlay(SpatailColor.borderHairline)
                    }
                }
            }
            .padding(.horizontal, SpatailSpace.s4)
            .padding(.vertical, SpatailSpace.s2)
            .background(Squircle(SpatailRadius.lg).fill(SpatailColor.surfaceCard))
            .overlay(Squircle(SpatailRadius.lg)
                .strokeBorder(SpatailColor.borderHairline, lineWidth: 1))
            .spatailShadow(.s2)
        }
    }

    /// Asks composed by the object-scope flow carry a machine context block —
    /// show only the human half in the history list.
    private func displayPrompt(_ prompt: String) -> String {
        prompt.components(separatedBy: "\n\n[object-context]").first ?? prompt
    }

    // MARK: Scene housekeeping (absorbed from the App/ stopgap Library)

    private var sceneCard: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s3) {
            Text("SCENE")
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)
            Text("Clearing removes every placed element. The room map and identified objects stay — placing again replans onto them.")
                .spatailType(.xs)
                .foregroundStyle(SpatailColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            SpatailButton(title: "Clear experience", variant: .secondary,
                          iconLeft: "trash") {
                model.clearExperience()
            }
        }
        .padding(SpatailSpace.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Squircle(SpatailRadius.lg).fill(SpatailColor.surfaceCard))
        .overlay(Squircle(SpatailRadius.lg)
            .strokeBorder(SpatailColor.borderHairline, lineWidth: 1))
        .spatailShadow(.s2)
    }

    private func problemBanner(_ text: String) -> some View {
        HStack(spacing: SpatailSpace.s2) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(SpatailColor.statusDanger)
            Text(text)
                .spatailType(.sm)
                .foregroundStyle(SpatailColor.textBody)
            Spacer()
            SpatailIconButton(systemName: "xmark", label: "Dismiss",
                              size: .sm, variant: .ghost) {
                placeProblem = nil
            }
        }
        .padding(SpatailSpace.s3)
        .background(Squircle(SpatailRadius.md).fill(SpatailColor.surfaceCard))
        .overlay(Squircle(SpatailRadius.md)
            .strokeBorder(SpatailColor.statusDanger, lineWidth: 1))
    }

    // MARK: Re-place — the saved contract against the room as scanned NOW

    private func place(_ entry: SavedExperience) {
        guard let contract = store.contract(for: entry.id) else {
            placeProblem = "Couldn't read “\(entry.title)” from disk — the saved contract is missing or corrupt."
            return
        }
        // Raw bytes ride along so a saved engine payload (rules/objectives)
        // re-instantiates its kits exactly like a fresh /modular response.
        runtime.apply(contract: contract, raw: store.contractData(for: entry.id),
                      arView: hub.arView,
                      surfaces: scanner.surfaces,
                      objects: registry.objects.filter(\.displayWorthy))
        dismiss()
    }
}

// MARK: - Card

private struct ExperienceCard: View {
    let entry: SavedExperience
    let featured: Bool
    let onPlace: () -> Void
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            Text(entry.anchoringMode == "object" ? "ON YOUR OBJECT" : "IN YOUR SPACE")
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)

            Text(entry.title.isEmpty ? entry.subject : entry.title)
                .spatailType(featured ? .h3 : .title)
                .foregroundStyle(SpatailColor.textStrong)
                .lineLimit(2)

            if !entry.prompt.isEmpty {
                Text(entry.prompt.components(separatedBy: "\n").first ?? entry.prompt)
                    .spatailType(.xs)
                    .foregroundStyle(SpatailColor.textMuted)
                    .lineLimit(featured ? 2 : 1)
            }

            HStack(spacing: SpatailSpace.s1) {
                SpatailBadge(text: "\(entry.stepCount) steps", tone: .neutral)
                SpatailBadge(text: "\(entry.assetCount) assets", tone: .neutral)
            }
            .padding(.top, SpatailSpace.s1)

            Spacer(minLength: SpatailSpace.s2)

            HStack(alignment: .center) {
                Text(entry.updatedAt, style: .date)
                    .spatailType(.micro)
                    .foregroundStyle(SpatailColor.textFaint)
                Spacer()
                SpatailButton(title: "Place", variant: .primary,
                              size: featured ? .md : .sm,
                              iconLeft: "arkit") {
                    onPlace()
                }
            }
        }
        .padding(SpatailSpace.s4)
        .frame(maxWidth: .infinity, minHeight: featured ? 170 : 190, alignment: .topLeading)
        .spatailGlass(tone: .light, radius: SpatailRadius.xl)
        .contextMenu {
            Button { onPlace() } label: {
                Label("Place in this room", systemImage: "arkit")
            }
            Button(role: .destructive) { onDelete() } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }
}
