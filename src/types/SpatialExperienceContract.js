// JSDoc typedefs for the SPATAIL SpatialExperienceContract.
// Used purely for IDE IntelliSense — no runtime cost. The closed
// vocabularies are mirrored from pipeline/spatail/experience_contract.js
// and schemas/spatialExperienceContract.schema.json (those three places
// must stay in lockstep).
//
// To use:
//   /** @type {import("../../src/types/SpatialExperienceContract.js").SpatialExperienceContract} */
//   const contract = await loadContract();

/**
 * @typedef {"two_d_panel"
 *   | "wall_dashboard"
 *   | "three_d_model"
 *   | "tabletop_model"
 *   | "floor_timeline"
 *   | "floating_decision_card"
 *   | "highlighted_target"
 *   | "exploded_view"
 *   | "anchored_callout"
 *   | "guide_line"
 *   | "diagnostic_overlay"} RepresentationMode
 */

/**
 * @typedef {"wall"
 *   | "table"
 *   | "floor"
 *   | "object_anchored"
 *   | "above_target"
 *   | "near_user"
 *   | "near_presenter"
 *   | "left_of_user"
 *   | "right_of_user"
 *   | "in_front_of_user"
 *   | "room_center"} PlacementKind
 */

/**
 * @typedef {"world_anchor"
 *   | "plane_anchor"
 *   | "object_anchor"
 *   | "relative_to_target"
 *   | "user_relative"
 *   | "simulated_anchor"} AnchorStrategy
 */

/**
 * @typedef {"real_scale"
 *   | "tabletop_scale"
 *   | "enlarged_detail"
 *   | "compact_panel"
 *   | "room_scale"} ScaleMode
 */

/**
 * @typedef {"ambient"
 *   | "persistent_context"
 *   | "active_focus"
 *   | "peripheral"
 *   | "on_demand"
 *   | "guiding"} AttentionBehavior
 */

/**
 * @typedef {object} Placement
 * @property {PlacementKind} kind
 * @property {string=}       anchor
 * @property {[number, number, number]=} position
 * @property {number[]=}     rotationDeg
 * @property {number[]=}     sizeMeters
 * @property {[number, number, number]=} from
 * @property {[number, number, number]=} to
 * @property {string=}       layout
 * @property {string=}       orientation
 */

/**
 * @typedef {object} ElementInteraction
 * @property {string} id
 * @property {string} type
 * @property {string} behavior
 */

/**
 * @typedef {object} SpatialElement
 * @property {string} id
 * @property {string} title
 * @property {string} contentType
 * @property {RepresentationMode} representationMode
 * @property {Placement}          placement
 * @property {AnchorStrategy}     anchorStrategy
 * @property {ScaleMode}          scaleMode
 * @property {number}             priority
 * @property {any}                sourceContent
 * @property {Array<object>}      requiredAssets
 * @property {string}             fallbackGeometry
 * @property {ElementInteraction[]} interactions
 * @property {AttentionBehavior}  attentionBehavior
 * @property {string}             whyThisRepresentation
 * @property {string}             whyThisPlacement
 */

/**
 * @typedef {"aligned_above"
 *   | "diagnoses"
 *   | "attached_to"
 *   | "controls_attention_for"
 *   | "connects"
 *   | "relates_to"} RelationshipType
 */

/**
 * @typedef {object} Relationship
 * @property {string} from
 * @property {string} to
 * @property {RelationshipType} type
 * @property {string=} note
 */

/**
 * @typedef {object} AttentionStep
 * @property {number} step
 * @property {string} focusElementId
 * @property {string} narration
 */

/**
 * @typedef {object} Interaction
 * @property {string} id
 * @property {string | null} elementId
 * @property {string} type
 * @property {string} behavior
 * @property {string} trigger
 */

/**
 * @typedef {object} SpatialExperienceContract
 * @property {"0.2.0-spatail"} schemaVersion
 * @property {string} createdAt
 * @property {string} experienceId
 * @property {string} title
 * @property {string} sourcePrompt
 * @property {Array<object>} sourceInputs
 * @property {Array<object>} sourceFiles
 * @property {{ name: string, confidence: string, source: string }} detectedDomain
 * @property {object} environmentAssumptions
 * @property {SpatialElement[]} spatialElements
 * @property {Relationship[]}   relationships
 * @property {{ interactions: Interaction[] }} interactionPlan
 * @property {AttentionStep[]}  attentionPlan
 * @property {Array<object>}    assetRequirements
 * @property {string}           reasoningSummary
 * @property {object}           vocabularies
 */

// Re-export the canonical enum arrays so consumers can validate without
// touching the pipeline implementation directly.
export {
  CONTENT_TYPES,
  REPRESENTATION_MODES,
  PLACEMENTS,
  ANCHOR_STRATEGIES,
  SCALE_MODES,
  ATTENTION_BEHAVIORS,
  SPATAIL_SCHEMA_VERSION,
} from "../../pipeline/spatail/experience_contract.js";
