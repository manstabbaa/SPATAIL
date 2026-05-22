// DemoSelectorView.swift
//
// Splash. Two clean cards — Mustang and Q3 — that route to the
// GeneratedExperienceView. The view is intentionally minimal:
// SPATAIL's product principle is that the spatial experience itself
// is the UI; phone screens are launchers.

import SwiftUI

struct DemoSelectorView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var loadError: String?

    var body: some View {
        ZStack {
            Color.spatailBg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    intro
                    ForEach(env.ingestion.availableContracts()) { ref in
                        NavigationLink(value: ref) {
                            DemoCard(ref: ref)
                        }
                        .buttonStyle(.plain)
                    }
                    if let err = loadError {
                        Text(err)
                            .font(.callout)
                            .foregroundColor(.red)
                    }
                    Spacer(minLength: 20)
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
            }
        }
        .navigationDestination(for: BundledContractRef.self) { ref in
            GeneratedExperienceView(contractRef: ref)
        }
        .navigationBarHidden(true)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            RoundedRectangle(cornerRadius: 4)
                .fill(LinearGradient(
                    colors: [.spatailAccent, .spatailAccent2],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
                .frame(width: 14, height: 14)
                .offset(y: 2)
            Text("SPATAIL")
                .font(.title2).fontWeight(.heavy)
                .tracking(0.8)
            Text("Spatial Experience Studio")
                .font(.caption)
                .foregroundColor(.spatailTextDim)
            Spacer()
        }
        .padding(.top, 16)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Content → spatial experience")
                .font(.title).bold()
            Text("Pick a demo card. SPATAIL decides which pieces stay readable, " +
                 "which become 3D, and where each piece lives around you.")
                .font(.callout)
                .foregroundColor(.spatailTextDim)
        }
    }
}

private struct DemoCard: View {
    let ref: BundledContractRef

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(ref.title)
                    .font(.title3).bold()
                Spacer()
                Image(systemName: "arrow.right.circle.fill")
                    .foregroundColor(.spatailAccent)
            }
            Text(domainLabel(for: ref.domain))
                .font(.caption)
                .padding(.horizontal, 8).padding(.vertical, 2)
                .background(Color.spatailAccent.opacity(0.12))
                .foregroundColor(.spatailAccent)
                .clipShape(Capsule())
            Text(pitch(for: ref.id))
                .font(.callout)
                .foregroundColor(.spatailTextDim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func domainLabel(for s: String) -> String {
        s.replacingOccurrences(of: "_", with: " ")
    }

    private func pitch(for id: String) -> String {
        switch id {
        case "mustang-service":
            return "Service workspace: vehicle status as readable panels, the " +
                   "air filter housing highlighted on the engine bay, the " +
                   "exploded assembly aligned directly above it."
        case "q3-manufacturing-review":
            return "Boardroom review: KPIs on the wall, factory process on " +
                   "the table, Q3 events as a walkable floor timeline, " +
                   "recommendations as floating decision cards."
        default:
            return ""
        }
    }
}
