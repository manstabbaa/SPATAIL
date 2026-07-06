#!/bin/zsh
# The off-device harness in one command (spec §0 law 5). Compiles the EXACT
# shipped sources (no copies) against macOS and runs the assert rig.
set -e
cd "$(dirname "$0")/.."
DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode.app} swiftc -O \
  Sources/Core/SpatailCore.swift \
  Sources/Perception/Spatial/GeometryFit.swift \
  Sources/Perception/Form/FormPriors.swift \
  Sources/Perception/Form/FormFitter.swift \
  Sources/Perception/Form/FormPointCloud.swift \
  Sources/Perception/KeyframeMath.swift \
  Sources/Registry/RegistryFusionMath.swift \
  Sources/Registry/RegistryCoherence.swift \
  Sources/Ask/AskPlanner.swift \
  Harness/main.swift -o "${TMPDIR:-/tmp}/form_harness"
"${TMPDIR:-/tmp}/form_harness"
