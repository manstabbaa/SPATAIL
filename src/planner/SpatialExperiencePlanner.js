// SpatialExperiencePlanner — public entry point.
//
// The five SPATAIL layers (ingest, understand, represent, place, reason)
// each live in pipeline/spatail/ as their implementation home, but the
// public planner API is published here. CLIs, the viewer's contract
// loader, and the future visionOS bundler all import from /src/planner/*
// so the underlying file layout can move without breaking consumers.

export { planExperience } from "../../pipeline/spatail/experience_planner.js";
export { ingestCard, ingestCardObject, probeAssetGroups } from "../../pipeline/spatail/content_ingestion.js";
