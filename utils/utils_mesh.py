from bpy.types import Object

def clean_unused_shapekeys(ob: Object, threshold: float = 0.005) -> list[str]:
    """
    Remove shape keys that don't meaningfully deform any vertices beyond the threshold.
    Threshold is in world space and automatically adjusted for unapplied object scale.
    """
    if not ob or ob.type != 'MESH':
        return []

    shape_keys = ob.data.shape_keys
    if not shape_keys or not hasattr(shape_keys, 'key_blocks') or len(shape_keys.key_blocks) == 0:
        return []

    avg_scale = (abs(ob.scale.x) + abs(ob.scale.y) + abs(ob.scale.z)) / 3.0
    local_threshold = threshold / avg_scale if avg_scale > 0 else threshold

    basis = shape_keys.key_blocks[0]
    basis_coords = [v.co.copy() for v in basis.data]

    removed = []
    for key in list(shape_keys.key_blocks)[1:]:
        key_name = key.name
        max_delta = round(max((v.co - basis_coords[i]).length for i, v in enumerate(key.data)), 6)

        if max_delta <= local_threshold:
            removed.append(key_name)
            ob.shape_key_remove(key)

    if removed:
        print(f"[ShapeKey] '{ob.name}': removed {len(removed)} unused keys: {', '.join(removed)}")

    if len(shape_keys.key_blocks) == 1:
        basis_name = basis.name
        ob.shape_key_remove(basis)
        print(f"[ShapeKey] '{ob.name}': removed sole basis '{basis_name}'")
        removed.append(basis_name)

    return removed
