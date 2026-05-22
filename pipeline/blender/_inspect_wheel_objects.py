import bpy, json
from mathutils import Vector

scn = bpy.context.scene
lo_all = Vector((float("inf"),)*3); hi_all = Vector((-float("inf"),)*3)
objs = []
for obj in bpy.data.objects:
    if obj.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT"): continue
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    lo_all = Vector(map(min, lo_all, lo))
    hi_all = Vector(map(max, hi_all, hi))
    objs.append({"name": obj.name, "type": obj.type,
                 "lo": [round(c,2) for c in lo], "hi": [round(c,2) for c in hi]})
info = {
    "object_bbox_centre": [round(c,2) for c in ((lo_all + hi_all) * 0.5)],
    "object_bbox_size":   [round(c,2) for c in (hi_all - lo_all)],
    "object_bbox_lo":     [round(c,2) for c in lo_all],
    "object_bbox_hi":     [round(c,2) for c in hi_all],
    "objects":            objs,
    "world_color_mgmt":   scn.view_settings.view_transform,
}
print("INFO_JSON_START")
print(json.dumps(info, indent=2))
print("INFO_JSON_END")
