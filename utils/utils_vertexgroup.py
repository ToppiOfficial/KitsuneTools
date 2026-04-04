import bpy
from bpy.types import Object, Bone, PoseBone, ArmatureBones, CurveMapping
from .utils_object import get_armature, get_armature_meshes


direction_naming_map = {
    '.L': '.R', '_L': '_R', 'Left': 'Right', '_Left': '_Right', '.Left': '.Right', 'L_': 'R_', 'L.': 'R.', 'L ': 'R ',
    '.R': '.L', '_R': '_L', 'Right': 'Left', '_Right': '_Left', '.Right': '.Left', 'R_': 'L_', 'R.': 'L.', 'R ': 'L '
}


def get_used_vertexgroups(mesh: Object, vertex_groups: set[int] | None = None,
                          tolerance: float = 0.001, respect_mirror: bool = True,
                          return_names = False,) -> set[int | str]:
    """
    Return the set of vertex group indices that are actually used (weight > tolerance).
    If respect_mirror is True, also include mirror pairs of used groups.
    """
    vgroup_used = set()
    vertex_groups_set = vertex_groups if vertex_groups is None else set(vertex_groups)
    vgroups = mesh.vertex_groups
    vgroups_len = len(vgroups)
    
    for v in mesh.data.vertices:
        for g in v.groups:
            if g.weight > tolerance and (vertex_groups_set is None or g.group in vertex_groups_set):
                vgroup_used.add(g.group)

    if respect_mirror and any(mod.type == 'MIRROR' for mod in mesh.modifiers):
        for idx in list(vgroup_used):
            if idx >= vgroups_len:
                continue
            vg_name = vgroups[idx].name
            
            for left_suffix, right_suffix in direction_naming_map.items():
                if left_suffix in vg_name:
                    opposite_name = vg_name.replace(left_suffix, right_suffix)
                    opposite_vg = vgroups.get(opposite_name)
                    if opposite_vg:
                        vgroup_used.add(opposite_vg.index)
                    break
                elif right_suffix in vg_name:
                    opposite_name = vg_name.replace(right_suffix, left_suffix)
                    opposite_vg = vgroups.get(opposite_name)
                    if opposite_vg:
                        vgroup_used.add(opposite_vg.index)
                    break
    
    if return_names:
        used_vgroup_names = {mesh.vertex_groups[i].name for i in vgroup_used if i < len(mesh.vertex_groups)}
        return used_vgroup_names
    else:
        return vgroup_used
    

def remove_unused_vertexgroups(ob: Object | None, bones: list[Bone] | ArmatureBones | None = None,
                               weight_limit: float = 0.001, respect_mirror: bool = True) -> dict[Object, list[str]] | None:
    """
    Clean vertex groups by:
      1. Removing very small weights below `weight_limit`.
      2. Removing unused vertex groups that are tied to bones.
      3. Keeping unused vertex groups that are NOT tied to bones.

    Args:
        ob: Object (mesh or armature) to clean
        bones: List of bones to consider, or None for all bones
        weight_limit: Minimum weight threshold
        respect_mirror: If True, preserve empty L/R groups when opposite side has weights

    Returns a dict mapping each mesh to the list of removed vertex group names.
    """
    if ob is None:
        return None
    
    removed_groups_per_mesh: dict[Object, list[str]] = {}

    if ob.type == 'MESH':
        meshes = [ob]
    elif ob.type == 'ARMATURE':
        meshes = get_armature_meshes(ob)
    else:
        return removed_groups_per_mesh
    
    armature = get_armature(ob)
    bone_names = {bone.name for bone in (bones or (armature.data.bones if armature else []))}

    for mesh in meshes:
        if not mesh.vertex_groups:
            continue

        removed_groups_per_mesh[mesh] = []

        used_groups = get_used_vertexgroups(
            mesh, 
            tolerance=weight_limit, 
            respect_mirror=respect_mirror,
        )
        
        vgroups = mesh.vertex_groups
        for vg in reversed(list(vgroups)):
            if vg.name in bone_names and vg.index not in used_groups:
                removed_groups_per_mesh[mesh].append(vg.name)
                vgroups.remove(vg)

    return removed_groups_per_mesh


def reapply_vertexgroup_as_curve(arm: Object, bones: PoseBone | list[PoseBone],
                                 curve: CurveMapping, invert: bool = False, vertex_group_target: str | None = None,
                                 min_weight_mask: float = 0.01, max_weight_mask: float = 1.0, normalize_to_parent: bool = True,
                                 constant_mask: bool = False, weight_threshold: float = 0.001,):
    """
    Apply a curve-based ramp to vertex weights along bones in an armature.
    
    Parameters
    ----------
    arm : Object
        Armature object containing the bones.
    bones : PoseBone | list[PoseBone]
        Bone or list of bones to apply the ramp to.
    curve : CurveMapping
        The Blender CurveMapping used to define the ramp along the bone.
    invert : bool, optional
        Flip the ramp direction along the bone axis.
    vertex_group_target : str | None, optional
        Target vertex group to receive leftover weight (residuals). If None, falls back to the bone's parent vertex group.
    min_weight_mask : float, optional
        Minimum original weight to include in the ramp. Vertices below this are ignored.
    max_weight_mask : float, optional
        Maximum original weight to include in the ramp. Vertices above this are ignored.
    normalize_to_parent : bool, optional
        Whether to scale the ramp by the original vertex weight.
    constant_mask : bool, optional
        If True, treat all eligible vertices as having full weight (1.0) before applying the ramp.
    weight_threshold : float, optional
        Minimum weight threshold when using constant_mask to avoid applying influence to noise vertices.
    """
    
    if arm.type != 'ARMATURE':
        return

    if not bones:
        print("ERROR: No bones selected.")
        return

    if isinstance(bones, PoseBone):
        bones = [bones]

    visible_only = getattr(bpy.context.scene.kitsunetools, "visible_mesh_only", False)
    for mesh_obj in get_armature_meshes(arm, visible_only=visible_only):
        mesh = mesh_obj.data
        mw = mesh_obj.matrix_world

        for bone in bones:
            bone_name = bone.name
            if bone_name not in mesh_obj.vertex_groups:
                continue

            vg = mesh_obj.vertex_groups[bone_name]
            if vg.lock_weight:
                continue

            head = mw @ bone.head
            tip = mw @ bone.tail
            line_vec = tip - head
            length = line_vec.length
            if length == 0:
                continue
            direction = line_vec.normalized()

            # Determine target vertex group for residuals
            target_vg = None
            if vertex_group_target:
                target_vg = mesh_obj.vertex_groups.get(vertex_group_target) or mesh_obj.vertex_groups.new(name=vertex_group_target)
            elif bone.parent:
                parent_name = bone.parent.name
                target_vg = mesh_obj.vertex_groups.get(parent_name) or mesh_obj.vertex_groups.new(name=parent_name)

            verts_to_update = []
            weights = []
            residuals = []

            for v in mesh.vertices:
                # Original weight as mask
                original_weight = next((g.weight for g in v.groups if g.group == vg.index), 0.0)
                
                if original_weight < weight_threshold:
                    continue  # skip noise
                
                if not (min_weight_mask <= original_weight <= max_weight_mask):
                    continue
                
                if constant_mask:
                    original_weight = 1.0

                world_co = mw @ v.co
                proj_len = max(0.0, (world_co - head).dot(direction))
                factor = min(proj_len / length, 1.0)
                
                factor = 1.0 - factor
                if invert:
                    factor = 1.0 - factor  # flip back to normal

                ramp_value = curve.evaluate(curve.curves[0], factor)
                ramp_weight = ramp_value * original_weight if normalize_to_parent else ramp_value

                verts_to_update.append(v.index)
                weights.append(ramp_weight)

                if target_vg:
                    residual = max(0.0, original_weight - ramp_weight)
                    residuals.append((v.index, residual))

            # Apply ramp weights
            if verts_to_update:
                vg.remove(verts_to_update)
                for v_idx, w in zip(verts_to_update, weights):
                    vg.add([v_idx], w, 'REPLACE')

            # Apply residuals to target
            if target_vg and residuals:
                for idx, w in residuals:
                    if w > 0:
                        target_vg.add([idx], w, 'ADD')