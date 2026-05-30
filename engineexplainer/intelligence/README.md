# Intelligence layer

Turns a user prompt into a **spatial contract** (see [../contracts/schema/spatial-contract.schema.json](../contracts/schema/spatial-contract.schema.json)) that the web runtime can play.

## Flow

```
prompt ───▶ context_builder ───▶ mechanic ───▶ director ───▶ critic ───▶ contract.json
                  │                  │            │           │
                  │                  │            │           └─ pass/revise loop (max 2)
                  │                  │            └─ uses tool-emit pattern: only valid actions
                  │                  └─ writes prose answer; no spatial decisions yet
                  └─ assembles registry, history, scene state into one window
```

Four roles, three of them LLM calls:

| Role | Input | Output | Why it exists |
|---|---|---|---|
| `context_builder` | prompt, part_registry, animation_library, history | flat context object | Single source of truth — every LLM call gets the same world view |
| `mechanic` | context | plain-language technical answer (`explanation` block) | Separates *being correct mechanically* from *being shown well* |
| `director` | context + mechanic.explanation | draft contract (beats + actions) | Stages the answer as visual storytelling |
| `critic` | draft contract + context | `OK` or list of revisions | Cheap LLM validator: catches references to non-existent parts/animations |

Each role has its system prompt in `prompts/` so they can be tuned independently of code.

## Entry point

```python
from engineexplainer.intelligence.orchestrator import answer

contract = answer(prompt="How does a piston work?")
# → dict that conforms to spatial-contract.schema.json
```

## Tool interface for the director

The director doesn't free-write JSON. It calls **typed action constructors** that map 1-to-1 to the contract schema's action types:

```python
ctx.highlight("piston_1A", color="#5046E5")
ctx.dim_others(except_=["piston_1A"])
ctx.play_animation("piston_1A_stroke", from_=0, to=0.5, rate=0.6)
ctx.move_camera(preset="cylinder_close")
ctx.label("piston_1A", text="Piston", kicker="COMPONENT", anchor="above")
ctx.show_panel("ExplanationCard", anchor="screen-top-right",
               title="Power stroke", body="...")
```

These constructors *validate at call time* — if the director references `piston_99` (which doesn't exist), it errors immediately instead of producing a bad contract.

## Anti-goals

- ❌ **Don't bake content into prompts.** Engine facts live in the part registry + classification, not in the system prompt. New engine → same prompts, new registry.
- ❌ **Don't let the director write raw JSON.** Always go through the tool interface.
- ❌ **Don't render anything.** Intelligence ends at JSON. The runtime renders.
