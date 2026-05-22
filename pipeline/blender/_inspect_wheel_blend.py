"""Inspect wheel.blend — print scene info so the render script knows
what's in it (frame range, cameras, collections, engine)."""
import bpy, sys, json

scn = bpy.context.scene
info = {
    "scene": scn.name,
    "frame_start": scn.frame_start,
    "frame_end": scn.frame_end,
    "fps": scn.render.fps,
    "render_engine": scn.render.engine,
    "active_camera": scn.camera.name if scn.camera else None,
    "all_cameras": [o.name for o in bpy.data.objects if o.type == "CAMERA"],
    "collections": sorted([c.name for c in bpy.data.collections]),
    "object_count": len(bpy.data.objects),
    "world_color_mgmt": scn.view_settings.view_transform,
}
# Look specifically for attention.cam.* pattern
info["attention_cams"] = sorted([n for n in info["all_cameras"]
                                 if "attention" in n.lower() or "cam" in n.lower()
                                 or "beat" in n.lower()])
info["attention_collections"] = sorted([n for n in info["collections"]
                                        if "attention" in n.lower() or "cam" in n.lower()
                                        or "beat" in n.lower()])
# Any markers in the scene?
info["timeline_markers"] = [(m.name, m.frame, m.camera.name if m.camera else None)
                            for m in scn.timeline_markers]
print("WHEEL_INFO_JSON_START")
print(json.dumps(info, indent=2))
print("WHEEL_INFO_JSON_END")
