import bpy
from mathutils import Vector, Matrix
from bpy.app.handlers import persistent

_prev_matrices: dict = {}
_is_mirroring: bool = False


def _find_mirror_bone(armature, source_bone, tolerance):
    src = source_bone.bone.head_local
    target = Vector((-src.x, src.y, src.z))
    best, best_dist = None, float("inf")

    solo_collections = [col for col in armature.data.collections_all if col.is_solo]

    for pb in armature.pose.bones:
        if pb == source_bone:
            continue

        bone = pb.bone
        if bone.hide:
            continue

        if bone.collections:
            if solo_collections:
                if not any(col in solo_collections for col in bone.collections):
                    continue
            else:
                if not any(col.is_visible for col in bone.collections):
                    continue

        dist = (bone.head_local - target).length
        if dist < tolerance and dist < best_dist:
            best_dist = dist
            best = pb

    return best


def _copy_mirrored_pose(source_pb, target_pb):
    src = source_pb.matrix_basis
    loc = src.to_translation()
    rot = src.to_quaternion()
    sca = src.to_scale()

    loc.x = -loc.x
    rot.y = -rot.y
    rot.z = -rot.z

    target_pb.matrix_basis = (
        Matrix.Translation(loc)
        @ rot.to_matrix().to_4x4()
        @ Matrix.Diagonal(sca).to_4x4()
    )

    if target_pb.rotation_mode == "QUATERNION":
        target_pb.rotation_quaternion = rot
    elif target_pb.rotation_mode == "AXIS_ANGLE":
        axis, angle = rot.to_axis_angle()
        target_pb.rotation_axis_angle = (angle, -axis.x, axis.y, axis.z)
    else:
        target_pb.rotation_euler = rot.to_euler(target_pb.rotation_mode)

    target_pb.location = loc
    target_pb.scale = sca


@persistent
def mirror_pose_handler(scene, depsgraph):
    global _is_mirroring

    if _is_mirroring:
        return

    context = bpy.context
    if context.mode != 'POSE':
        return

    obj = context.object
    if not obj or obj.type != 'ARMATURE':
        return

    props = obj.data.kitsunetools
    if not props.x_mirror_pose:
        return

    # If the user re-enabled Blender's built-in mirror, disable ours
    if obj.data.use_mirror_x:
        props.x_mirror_pose = False
        return

    selected = context.selected_pose_bones
    if not selected:
        return

    prev = _prev_matrices.setdefault(obj.name, {})

    moved = []
    for pb in selected:
        current = pb.matrix_basis.copy()
        if pb.name in prev and prev[pb.name] != current:
            moved.append(pb)
        prev[pb.name] = current

    if not moved:
        return

    _is_mirroring = True
    try:
        for pb in moved:
            mirror = _find_mirror_bone(obj, pb, props.x_mirror_tolerance)
            if mirror:
                _copy_mirrored_pose(pb, mirror)
    finally:
        _is_mirroring = False