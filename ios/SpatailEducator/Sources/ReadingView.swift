import SwiftUI
#if os(iOS)
import UIKit
#endif

// ReadingView — the Educational Wrapper reading interface. Shows the bundled
// reader's concept pages; the learner taps a concept chip OR selects any text in
// the page, then taps "Show in SPATAIL" to turn the selection into an interactive
// spatial explanation. It does NOT build AR — it hands the selection to
// SessionModel.explainSelection, which runs the existing SPATAIL pipeline and
// presents via the existing RepresentationRuntime.

struct ReadingView: View {
    @ObservedObject var model: SessionModel
    @State private var index = 0
    @State private var selection: CapturedSelection?

    private var concepts: [ReaderConcept] { EducationalReader.concepts }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(Color.white.opacity(0.1))
            if !concepts.isEmpty {
                let concept = concepts[min(index, concepts.count - 1)]
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(concept.title).font(.title2).bold()
                        if !concept.learningGoal.isEmpty {
                            Text(concept.learningGoal).font(.callout).foregroundStyle(.secondary)
                        }
                        chips(concept)
                        selectableBody(concept)
                        Color.clear.frame(height: 90)        // clearance above the selection bar
                    }
                    .padding()
                }
            } else {
                Spacer()
                Text("No reading content bundled.").foregroundStyle(.secondary)
                Spacer()
            }
            selectionBar
        }
        .background(Color(white: 0.07).ignoresSafeArea())
        .foregroundStyle(.white)
    }

    private var header: some View {
        HStack {
            Button { model.reset() } label: { Label("Close", systemImage: "xmark") }.font(.callout)
            Spacer()
            Text(EducationalReader.book.title).font(.headline).lineLimit(1)
            Spacer()
            HStack(spacing: 14) {
                Button { index = max(0, index - 1); selection = nil } label: {
                    Image(systemName: "chevron.left")
                }.disabled(index == 0)
                Text("\(min(index + 1, max(concepts.count, 1)))/\(max(concepts.count, 1))")
                    .font(.caption).foregroundStyle(.secondary)
                Button { index = min(concepts.count - 1, index + 1); selection = nil } label: {
                    Image(systemName: "chevron.right")
                }.disabled(index >= concepts.count - 1)
            }
        }
        .padding(.horizontal).padding(.vertical, 10)
    }

    @ViewBuilder private func chips(_ concept: ReaderConcept) -> some View {
        if !concept.selectionTargets.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Tap a concept — or select any text below:")
                    .font(.caption2).foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(concept.selectionTargets, id: \.self) { target in
                            Button {
                                selection = CapturedSelection(
                                    selectedText: target, surroundingContext: concept.body,
                                    sourceType: "book_page", pageNumber: concept.page)
                            } label: {
                                Text(target).font(.caption)
                                    .padding(.horizontal, 12).padding(.vertical, 6)
                                    .background(Color.accentColor.opacity(0.3), in: Capsule())
                            }.buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private func selectableBody(_ concept: ReaderConcept) -> some View {
        #if os(iOS)
        ReadingTextView(text: concept.body, pageNumber: concept.page, selection: $selection)
            .frame(minHeight: 240)
        #else
        Text(concept.body).font(.body).textSelection(.enabled)
        #endif
    }

    @ViewBuilder private var selectionBar: some View {
        if let sel = selection, !sel.selectedText.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Divider().overlay(Color.white.opacity(0.1))
                Text("Selected: “\(sel.selectedText)”").font(.subheadline).bold().lineLimit(2)
                Button { model.explainSelection(sel) } label: {
                    HStack {
                        Image(systemName: "sparkles")
                        Text("Show in SPATAIL").bold()
                        Spacer()
                        Image(systemName: "arkit")
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
            .padding()
            .background(.ultraThinMaterial)
        }
    }
}

#if os(iOS)
// TextSelectionController — captures the selected substring + its surrounding
// paragraph from a read-only UITextView (the spec's selection-capture component).
struct ReadingTextView: UIViewRepresentable {
    let text: String
    let pageNumber: Int
    @Binding var selection: CapturedSelection?

    func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.isEditable = false
        tv.isSelectable = true
        tv.isScrollEnabled = false              // the SwiftUI ScrollView handles scrolling
        tv.backgroundColor = .clear
        tv.textColor = .white
        tv.font = .preferredFont(forTextStyle: .body)
        tv.textContainerInset = .init(top: 8, left: 2, bottom: 8, right: 2)
        tv.delegate = context.coordinator
        return tv
    }

    func updateUIView(_ tv: UITextView, context: Context) {
        if tv.text != text { tv.text = text }
        context.coordinator.pageNumber = pageNumber
    }

    func makeCoordinator() -> Coordinator { Coordinator($selection) }

    final class Coordinator: NSObject, UITextViewDelegate {
        var pageNumber = 0
        private let selection: Binding<CapturedSelection?>
        init(_ selection: Binding<CapturedSelection?>) { self.selection = selection }

        func textViewDidChangeSelection(_ tv: UITextView) {
            guard let r = tv.selectedTextRange, !r.isEmpty,
                  let s = tv.text(in: r)?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !s.isEmpty else { return }
            let ns = (tv.text ?? "") as NSString
            let ctx = ns.substring(with: ns.paragraphRange(for: tv.selectedRange))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            selection.wrappedValue = CapturedSelection(
                selectedText: s, surroundingContext: ctx,
                sourceType: "book_page", pageNumber: pageNumber)
        }
    }
}
#endif
