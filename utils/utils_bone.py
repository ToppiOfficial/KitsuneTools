import bpy, mathutils, re
from bpy.types import Bone, PoseBone, Object
from .utils_contextmanagers import is_addon_enabled, selfreport, report, preserve_context_mode, preserve_armature_state
from .utils_object import is_armature, get_armature
from .utils_armature import get_armature_meshes

bonename_direction_map = {
    '.L': '.R', '_L': '_R', 'Left': 'Right', '_Left': '_Right', '.Left': '.Right', 'L_': 'R_', 'L.': 'R.', 'L ': 'R ',
    '.R': '.L', '_R': '_L', 'Right': 'Left', '_Right': '_Left', '.Right': '.Left', 'R_': 'L_', 'R.': 'L.', 'R ': 'L '
}

exportname_shortcut_keywords = {
    "vbip": "ValveBiped.Bip01"
}

# Only when KitsuneSrcTool is installed
def get_bone_exportname(bone: Bone | PoseBone | None, for_write=False) -> str:
    _EXPORTNAME_MODULES = (
        "...kitsune_source_tools.utils",
        "...io_scene_valvesource.utils",
    )

    if is_addon_enabled("kitsune_source_tools"):
        for module_path in _EXPORTNAME_MODULES:
            try:
                from importlib import import_module
                mod = import_module(module_path, package=__package__)
                return mod.get_bone_exportname(bone, for_write=for_write)
            except (ModuleNotFoundError, ImportError, AttributeError):
                continue

    if bone is None:
        return "None"
    return bone.name if hasattr(bone, "name") else str(bone)


def get_canonical_bonename(export_name: str) -> str:
    """Convert an exported bone name back to its canonical form:
       - Replaces directional markers with ' * '
       - Converts expanded shortcut names back to '!shortcut!' form
       - Converts underscores to spaces
       - Collapses multiple spaces into a single space
    """
    # Reverse shortcut expansion
    reversed_shortcuts = {v: k for k, v in exportname_shortcut_keywords.items()}
    for full, shortcut in reversed_shortcuts.items():
        export_name = export_name.replace(full, f"!{shortcut}!")

    for k, v in bonename_direction_map.items():
        export_name = export_name.replace(k, " * ")


    export_name = export_name.replace("_", " ")
    export_name = re.sub(r'\s+', ' ', export_name).strip()

    return export_name


@selfreport
def subdivide_bone(bone: str | list, armature : Object, subdivisions: int = 2, falloff: int = 10, smoothness: float = 0.0,
                   weights_only: bool = False, force_locked: bool = False, skip_original_bone: bool = True):
    """
    Split a bone using Blender's native subdivide with automatic weight distribution.
    
    Args:
        bone: EditBone or list of EditBones to split
        subdivisions: Number of segments to split the bone into (minimum 2)
        falloff: Power factor for weight falloff curve (higher = sharper transitions)
        smoothness: Weight smoothing amount (0 = no smoothing, higher = smoother transitions)
        weights_only: If True, only redistribute weights without creating new bones
        force_locked: If True, modify weights even if vertex groups are locked
        skip_original_bone: If True (default), start from .001 in weights_only mode, leaving original bone weights intact
    """
    
    def get_bone_chain(original_bone, eb):
        chain = []
        current = original_bone
        while current:
            chain.append(current)
            children = [b for b in eb if b.parent == current and b.use_connect]
            current = children[0] if children else None
        return chain
    
    def generate_bone_names(base_name, subdivisions, weights_only, skip_original_bone):
        names = []
        for i in range(1, subdivisions + 1):
            if weights_only and skip_original_bone:
                names.append(f"{base_name}.{i:03d}")
            else:
                if i == 1:
                    names.append(base_name)
                else:
                    names.append(f"{base_name}.{i:03d}")
        return names
    
    def collect_vertex_data(meshes, old_bone_name, force_locked, arm_matrix, bone_head, bone_tail):
        all_vertex_data = []
        bone_vec = bone_tail - bone_head
        bone_length_sq = bone_vec.length_squared
        
        for mesh in meshes:
            if old_bone_name not in mesh.vertex_groups:
                continue
            
            vg_old = mesh.vertex_groups[old_bone_name]
            if not force_locked and vg_old.lock_weight:
                report('INFO', f"Skipping mesh '{mesh.name}': vertex group '{old_bone_name}' is locked")
                continue
                
            mesh_matrix = mesh.matrix_world
            
            for vert in mesh.data.vertices:
                for group in vert.groups:
                    if group.group == vg_old.index:
                        pos_world = mesh_matrix @ vert.co
                        vec_to_vert = pos_world - bone_head
                        
                        t = vec_to_vert.dot(bone_vec) / bone_length_sq if bone_length_sq > 0 else 0
                        t = max(0.0, min(1.0, t))
                        
                        all_vertex_data.append({
                            'mesh': mesh,
                            'vert_index': vert.index,
                            'weight': group.weight,
                            't': t
                        })
        
        return all_vertex_data
    
    def create_vertex_groups(meshes, old_bone_name, bone_names, force_locked, weights_only):
        mesh_vg_map = {}
        
        for mesh in meshes:
            if old_bone_name not in mesh.vertex_groups:
                continue
            
            vg_old = mesh.vertex_groups[old_bone_name]
            if not force_locked and vg_old.lock_weight:
                continue
            
            vg_list = []
            for bone_name in bone_names:
                if bone_name in mesh.vertex_groups:
                    existing_vg = mesh.vertex_groups[bone_name]
                    
                    if weights_only and bone_name != old_bone_name:
                        has_weights = False
                        for vert in mesh.data.vertices:
                            for group in vert.groups:
                                if group.group == existing_vg.index and group.weight > 0:
                                    has_weights = True
                                    break
                            if has_weights:
                                break
                        
                        if has_weights:
                            report('WARNING', f"Vertex group '{bone_name}' in mesh '{mesh.name}' already has weights (weights_only mode)")
                    
                    vg_list.append(existing_vg)
                else:
                    vg_list.append(mesh.vertex_groups.new(name=bone_name))
            
            mesh_vg_map[mesh] = vg_list
        
        return mesh_vg_map
    
    def apply_smoothing(influences, smooth_amount):
        smoothed = influences.copy()
        
        for _ in range(int(smooth_amount)):
            temp = []
            for i in range(len(smoothed)):
                kernel_sum = smoothed[i]
                kernel_count = 1.0
                
                if i > 0:
                    kernel_sum += smoothed[i - 1]
                    kernel_count += 1.0
                if i < len(smoothed) - 1:
                    kernel_sum += smoothed[i + 1]
                    kernel_count += 1.0
                
                temp.append(kernel_sum / kernel_count)
            smoothed = temp
        
        fractional = smooth_amount - int(smooth_amount)
        if fractional > 0.0:
            temp = []
            for i in range(len(smoothed)):
                kernel_sum = smoothed[i]
                kernel_count = 1.0
                
                if i > 0:
                    kernel_sum += smoothed[i - 1]
                    kernel_count += 1.0
                if i < len(smoothed) - 1:
                    kernel_sum += smoothed[i + 1]
                    kernel_count += 1.0
                
                temp.append(kernel_sum / kernel_count)
            
            smoothed = [s * (1.0 - fractional) + t * fractional for s, t in zip(smoothed, temp)]
        
        return smoothed
    
    def distribute_weights(all_vertex_data, mesh_vg_map, num_bones, falloff_power, smooth_amount):
        vertex_weights = {}
        vertices_to_clear = set()
        
        for data in all_vertex_data:
            t = data['t']
            mesh = data['mesh']
            vert_index = data['vert_index']
            weight = data['weight']
            vg_list = mesh_vg_map[mesh]
            
            segment_centers = [(i + 0.5) / num_bones for i in range(num_bones)]
            influences = [(1.0 - abs(t - center)) ** falloff_power if abs(t - center) < 1.0 else 0.0 
                         for center in segment_centers]
            
            if smooth_amount > 0.0:
                influences = apply_smoothing(influences, smooth_amount)
            
            total = sum(influences)
            if total == 0.0:
                continue
            
            normalized = [inf / total for inf in influences]
            filtered = [w if w * weight >= 0.0001 else 0.0 for w in normalized]
            
            total_filtered = sum(filtered)
            if total_filtered > 0.0:
                filtered = [w / total_filtered for w in filtered]
            
            vertices_to_clear.add((mesh, vert_index, vg_list[0]))
            
            if (mesh, vert_index) not in vertex_weights:
                vertex_weights[(mesh, vert_index)] = []
            
            for i, w_norm in enumerate(filtered):
                final_w = weight * w_norm
                if final_w >= 0.0001:
                    vertex_weights[(mesh, vert_index)].append((vg_list[i], final_w))
        
        return vertex_weights, vertices_to_clear
    
    def apply_weights(vertex_weights, vertices_to_clear, meshes, old_bone_name, bone_names, force_locked):
        for mesh, vert_index, vg in vertices_to_clear:
            vg.remove([vert_index])
        
        for (mesh, vert_index), weights in vertex_weights.items():
            for vg, final_w in weights:
                vg.add([vert_index], final_w, 'REPLACE')
        
        bone_names_set = set(bone_names)
        for mesh in meshes:
            if old_bone_name in mesh.vertex_groups and old_bone_name not in bone_names_set:
                vg_old = mesh.vertex_groups[old_bone_name]
                if force_locked or not vg_old.lock_weight:
                    mesh.vertex_groups.remove(vg_old)
    
    def copy_bone_props(arm, original_name, new_bone_names):
        original_bone_data = arm.data.bones.get(original_name)
        if original_bone_data and hasattr(original_bone_data, 'vs'):
            original_props = original_bone_data.vs
            
            for new_name in new_bone_names:
                new_bone_data = arm.data.bones.get(new_name)
                if new_bone_data and hasattr(new_bone_data, 'vs'):
                    new_props = new_bone_data.vs
                    new_props.ignore_rotation_offset = original_props.ignore_rotation_offset
                    new_props.export_rotation_offset_x = original_props.export_rotation_offset_x
                    new_props.export_rotation_offset_y = original_props.export_rotation_offset_y
                    new_props.export_rotation_offset_z = original_props.export_rotation_offset_z
                    new_props.ignore_location_offset = original_props.ignore_location_offset
                    new_props.export_location_offset_x = original_props.export_location_offset_x
                    new_props.export_location_offset_y = original_props.export_location_offset_y
                    new_props.export_location_offset_z = original_props.export_location_offset_z
    
    if armature is None: return None

    if bpy.context.active_object.mode != 'EDIT':
        return
    
    subdivisions = max(2, subdivisions)
    
    if isinstance(bone, list):
        props_to_copy = []
        
        for b in bone:
            result = subdivide_bone(b, armature, subdivisions, falloff, smoothness, weights_only, force_locked, skip_original_bone)
            if result:
                props_to_copy.append(result)
        
        if props_to_copy and not weights_only:
            arm = get_armature(bone[0])
            if arm:
                bpy.ops.object.mode_set(mode='OBJECT')
                for original_name, new_names in props_to_copy:
                    copy_bone_props(arm, original_name, new_names)
                bpy.ops.object.mode_set(mode='EDIT')
        
        return

    bone = armature.data.edit_bones.get(bone)

    if bone is None: return None
    
    with preserve_armature_state(armature, reset_pose=True):
        meshes = get_armature_meshes(armature, visible_only=bpy.context.scene.kitsunetools.visible_mesh_only)
        if not meshes:
            return
        try:
            old_bone_name = bone.name
            bone_head = armature.matrix_world @ bone.head.copy()
            bone_tail = armature.matrix_world @ bone.tail.copy()
            eb = armature.data.edit_bones
            
            if weights_only:
                bone_chain = None
                bone_names = generate_bone_names(old_bone_name, subdivisions, weights_only, skip_original_bone)
            else:
                eb.active = bone
                bpy.ops.armature.select_all(action='DESELECT')
                bone.select = True
                bone.select_head = True
                bone.select_tail = True
                
                bpy.ops.armature.subdivide(number_cuts=subdivisions - 1)
                bone_chain = get_bone_chain(bone, eb)
                
                if len(bone_chain) != subdivisions:
                    report('WARNING', f"Expected {subdivisions} bones but got {len(bone_chain)}")
                
                bone_names = [b.name for b in bone_chain]
            
            all_vertex_data = collect_vertex_data(meshes, old_bone_name, force_locked, armature.matrix_world, bone_head, bone_tail)
            mesh_vg_map = create_vertex_groups(meshes, old_bone_name, bone_names, force_locked, weights_only)
            vertex_weights, vertices_to_clear = distribute_weights(all_vertex_data, mesh_vg_map, subdivisions, falloff, smoothness)
            apply_weights(vertex_weights, vertices_to_clear, meshes, old_bone_name, bone_names, force_locked)
            
        except Exception as e:
            report('ERROR', f"Failed to subdivide bone '{bone.name}': {e}")
            return None

        if not weights_only and bone_chain:
            new_bone_names = [b.name for b in bone_chain]
            
            bpy.ops.object.mode_set(mode='OBJECT')
            copy_bone_props(armature, old_bone_name, new_bone_names)
            bpy.ops.object.mode_set(mode='EDIT')
            
            return (old_bone_name, new_bone_names)
        
        return None
    

def remove_bone(arm: Object, bone: str | list[str],  source: str | None = None,
                match_parent_to_head: bool = False, match_parent_to_head_tolerance: float = 3e-5) -> None:
    
    def _find_final_tail(edit_bone, bones_to_remove, tolerance):
        current = edit_bone
        
        while current.children:
            next_child = None
            for child in current.children:
                if child.name in bones_to_remove and (child.head - current.tail).length <= tolerance:
                    next_child = child
                    break
            
            if next_child:
                current = next_child
            else:
                break
        
        return current.tail

    def _adjust_parent_tail(edit_bone, tolerance, bones_to_remove):
        parent = edit_bone.parent
        parent.use_connect = False
        
        if len(parent.children) == 1:
            tail_position = _find_final_tail(edit_bone, bones_to_remove, tolerance)
            parent.tail = tail_position
        elif len(parent.children) > 1:
            for child in edit_bone.children:
                if (child.head - edit_bone.tail).length <= tolerance:
                    tail_position = _find_final_tail(edit_bone, bones_to_remove, tolerance)
                    parent.tail = tail_position
                    break

    def _remove_single_bone(arm, bone_name, source, match_parent_to_head, tolerance, bones_to_remove):
        edit_bone = arm.data.edit_bones.get(bone_name)
        if not edit_bone:
            return

        edit_bone.use_connect = False
        
        for child in edit_bone.children:
            child.use_connect = False

        if match_parent_to_head and edit_bone.parent:
            _adjust_parent_tail(edit_bone, tolerance, bones_to_remove)

        if source:
            source_bone = arm.data.edit_bones.get(source)
            if source_bone:
                for child in edit_bone.children:
                    child.parent = source_bone

        arm.data.edit_bones.remove(edit_bone)

    if not is_armature(arm):
        return

    with preserve_armature_state(arm,reset_pose=False):
        bones_to_remove = {bone} if isinstance(bone, str) else set(bone)
        
        if isinstance(bone, str):
            _remove_single_bone(arm, bone, source, match_parent_to_head, match_parent_to_head_tolerance, bones_to_remove)
        elif isinstance(bone, (list, tuple, set)):
            for entry in bone:
                _remove_single_bone(arm, entry, source, match_parent_to_head, match_parent_to_head_tolerance, bones_to_remove)


def merge_bones(armature: Object, source: Bone, target: Bone | list[Bone], keep_bone: bool = False,  
                visible_mesh_only: bool = False,  keep_original_weight: bool = False,
                centralize_bone: bool = False) -> tuple[set[str], list[tuple[str, str]], set[str]]:
    """
    Merges bones by transferring vertex weights, constraints, and reparenting children.

    This function can operate on a single target bone or an iterable of target bones.
    It's recursive when handling an iterable of targets to correctly determine the
    parent for merging in sequence.

    Args:
        armature: The armature object.
        source: The bone to merge into. If None, it's determined from the target's parent.
        target: A single bone or an iterable of bones to be merged.
        keep_bone: If True, the target bone is not removed after merging.
        visible_mesh_only: If True, only visible meshes are considered for weight merging.
        keep_original_weight: If True, weights are copied, not moved. Implies keep_bone=True.
        centralize_bone: If True, prepares data for bone centralization.

    Returns:
        A tuple containing:
        - A set of names of bones that were removed.
        - A list of (source, target) name pairs for centralization.
        - A set of names of vertex groups that were processed.
    """
    def _find_valid_parent(bone: Bone, bones_to_remove: set[str]) -> Bone | None:
        """
        Finds the first parent of a bone that is not in the set of bones to be removed.
        """
        parent = bone.parent
        while parent and parent.name in bones_to_remove:
            parent = parent.parent
        return parent

    def _merge_vertex_groups(source_bone: Bone,target_bone: Bone,processed_groups: set[str],):
        """
        Merges vertex weights from the target bone's group to the source bone's group
        on all meshes associated with the armature.
        """
        for mesh in get_armature_meshes(armature):
            if visible_mesh_only and not mesh.visible_get():
                continue

            vgs = mesh.vertex_groups
            target_group = vgs.get(target_bone.name)
            if not target_group:
                continue

            source_group = vgs.get(source_bone.name)
            if not source_group:
                source_group = vgs.new(name=source_bone.name)
            
            target_group_index = target_group.index

            # Optimized loop to gather vertex weights
            weights_to_add = []
            for v in mesh.data.vertices:
                for g in v.groups:
                    if g.group == target_group_index:
                        weights_to_add.append((v.index, g.weight))
                        break  # Vertex found in group, move to the next vertex

            if not weights_to_add:
                if not keep_original_weight:
                    vgs.remove(target_group)
                continue
                
            for vertex_index, weight in weights_to_add:
                source_group.add([vertex_index], weight, 'ADD')

            processed_groups.add(target_bone.name)

            if not keep_original_weight:
                vgs.remove(target_group)

    def _update_constraints(old_target: str, new_target: str):
        """
        Updates bone constraints that target `old_target` to point to `new_target`.
        """
        if not armature.pose:
            return
            
        for pose_bone in armature.pose.bones:
            for constraint in pose_bone.constraints:
                if hasattr(constraint, "subtarget") and constraint.subtarget == old_target:
                    constraint.subtarget = new_target

    bones_to_remove = set()
    merged_pairs = []
    processed_groups = set()

    if not keep_bone:
        keep_original_weight = False

    # Handle multiple targets recursively
    if isinstance(target, list) and not isinstance(target, (str, Bone)):
        for entry in target:
            # Determine source for this entry, respecting prior merges in this run
            entry_source = source or _find_valid_parent(entry, bones_to_remove)
            if not entry_source:
                continue

            # Recursive call for each target in the iterable
            res_rem, res_pairs, res_proc = merge_bones(
                armature, entry_source, entry, keep_bone,
                visible_mesh_only, keep_original_weight, centralize_bone
            )
            bones_to_remove.update(res_rem)
            merged_pairs.extend(res_pairs)
            processed_groups.update(res_proc)
        
        return bones_to_remove, merged_pairs, processed_groups

    # Handle a single target
    # If source is not provided, find the first valid parent that is not scheduled for removal
    if source is None:
        source = _find_valid_parent(target, bones_to_remove)
        if not source:
            # Cannot merge if there is no parent to merge into
            return set(), [], set()

    _merge_vertex_groups(source, target, processed_groups)

    if not keep_bone:
        _update_constraints(target.name, source.name)
        bones_to_remove.add(target.name)

    if centralize_bone:
        merged_pairs.append((source.name, target.name))

    return bones_to_remove, merged_pairs, processed_groups


def centralize_bone_pairs(arm: Object, pairs: list, min_length: float = 1e-4):
    """
    For each (source, target) in pairs:
    - Centers source bone's head and tail between itself and the target's head/tail.
    - Ensures the resulting bone has at least `min_length`, otherwise skips adjustment.
    """
    if not is_armature(arm):
        return

    with preserve_context_mode(arm, "EDIT") as edit_bones, preserve_armature_state(arm,reset_pose=False):
        for src_name, tgt_name in pairs:
            if src_name not in edit_bones or tgt_name not in edit_bones:
                continue

            src_bone = edit_bones[src_name] # type: ignore
            tgt_bone = edit_bones[tgt_name] # type: ignore

            mid_head = (src_bone.head + tgt_bone.head) * 0.5
            mid_tail = (src_bone.tail + tgt_bone.tail) * 0.5

            if (mid_tail - mid_head).length < min_length:
                direction = (
                    (src_bone.tail - src_bone.head).normalized()
                    if (src_bone.tail - src_bone.head).length > 0
                    else mathutils.Vector((0, 0, 1))
                )
                mid_tail = mid_head + direction * min_length

            src_bone.head = mid_head
            src_bone.tail = mid_tail
