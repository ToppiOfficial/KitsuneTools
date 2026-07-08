import bpy, re
from mathutils import Matrix, Vector, Quaternion
from bpy.types import Object, Bone, PoseBone, EditBone
from .utils_object import get_armature_meshes, is_armature
from .utils_contextmanagers import selfreport, preserve_armature_state, preserve_context_mode, unhide_all_objects, report, copy_property_group, copy_addon_properties
from .utils_object import op_override, apply_armature_to_mesh_without_shape_keys, apply_armature_to_mesh_with_shapekeys, reevaluate_bone_parented_empty_matrix
from .utils_vertexgroup import get_used_vertexgroups


def sort_bones_by_hierarchy(bones: list[Bone]) -> list[Bone]:
    bone_set = set(bones)
    sorted_bones = []
    visited = set()
    
    def dfs(bone):
        if bone in visited or bone not in bone_set:
            return
        visited.add(bone)
        sorted_bones.append(bone)
        
        for child in sorted(bone.children, key=lambda b: b.name):
            if child in bone_set:
                dfs(child)
    
    roots = [b for b in bone_set if b.parent is None or b.parent not in bone_set]
    
    for root in sorted(roots, key=lambda b: b.name):
        dfs(root)
    
    return sorted_bones


def get_selected_bones(armature : Object | None, bone_type : str = 'BONE', sort_type : str | None = 'TO_LAST',
                       exclude_active : bool = False, select_all : bool = False) -> list[Bone | PoseBone | EditBone | None]:
    """
    Returns bones from an armature with optional selection, visibility, and sorting filters.

    Args:
        armature (Object): Target armature object (must be type 'ARMATURE').
        bone_type (str, optional): Type of bones to return: 'BONE', 'EDITBONE', or 'POSEBONE'. 
                                   If invalid, it is inferred from the current mode.
        sort_type (str, optional): Sorting order: 'TO_LAST' (default), 'TO_FIRST', or no sorting.
        exclude_active (bool, optional): If True, exclude the active bone from the result.
        select_all (bool, optional): If True, ignore selection and visibility filters.

    Returns:
        list[Bone | EditBone | PoseBone]:
            A list of bone objects based on the filters applied.

    Notes:
        - Selection is checked in OBJECT mode.
        - If any bone collections are soloed, only those bones are returned.
        - If none are soloed, only bones from visible collections are included.
    """
    if not is_armature(armature): return []
    
    if bone_type not in ['BONE', 'EDITBONE', 'POSEBONE']:
        if armature.mode == 'EDIT': bone_type = 'EDITBONE'
        elif armature.mode == 'POSE': bone_type = 'POSEBONE'
        else: bone_type = 'BONE'
        
    if sort_type is None: sort_type = ''
    
    # we can evaluate the selected bones through object mode
    with preserve_context_mode(armature, 'OBJECT'): 
        selectedBones = []
        
        armatureBones = armature.data.bones
        armatureBoneCollections = armature.data.collections_all
        
        solo_BoneCollections = [col for col in armatureBoneCollections if col.is_solo]
        
        if exclude_active and armature.data.bones.active is not None:
            active_name = armature.data.bones.active.name
            armatureBones = [b for b in armatureBones if b.name != active_name]
            
        if sort_type in ['TO_LAST', 'TO_FIRST']:
            armatureBones = sort_bones_by_hierarchy(armatureBones)
            
            if sort_type == 'TO_FIRST':
                armatureBones.reverse()
        
        for bone in armatureBones:
            if not select_all:
                if bone.hide_select or not bone.select:
                    continue
                    
                if armatureBoneCollections and bone.collections:
                    boneCollections = bone.collections
                    # If there are solo collections, skip bones not in any of them
                    if solo_BoneCollections:
                        if not any(col in solo_BoneCollections for col in boneCollections):
                            continue
                    else:
                        # If no solo mode, skip bones in hidden collections
                        if not any(col.is_visible for col in boneCollections):
                            continue

            selectedBones.append(bone.name)
    
    if bone_type == 'POSEBONE': return [armature.pose.bones.get(b) for b in selectedBones]
    if bone_type == 'EDITBONE': return [armature.data.edit_bones.get(b) for b in selectedBones]
    else: return [armature.data.bones.get(b) for b in selectedBones]


def get_visible_bones(armature: Object | None, bone_type: str = 'BONE', sort_type: str | None = 'TO_LAST',
                      exclude_active: bool = False) -> list[Bone | PoseBone | EditBone | None]:
    
    if not is_armature(armature): return []

    if bone_type not in ['BONE', 'EDITBONE', 'POSEBONE']:
        if armature.mode == 'EDIT': bone_type = 'EDITBONE'
        elif armature.mode == 'POSE': bone_type = 'POSEBONE'
        else: bone_type = 'BONE'

    if sort_type is None: sort_type = ''

    with preserve_context_mode(armature, 'OBJECT'):
        armatureBones = list(armature.data.bones)
        armatureBoneCollections = armature.data.collections_all
        solo_BoneCollections = [col for col in armatureBoneCollections if col.is_solo]

        if exclude_active and armature.data.bones.active is not None:
            active_name = armature.data.bones.active.name
            armatureBones = [b for b in armatureBones if b.name != active_name]

        if sort_type in ['TO_LAST', 'TO_FIRST']:
            armatureBones = sort_bones_by_hierarchy(armatureBones)
            if sort_type == 'TO_FIRST':
                armatureBones.reverse()

        visibleBones = []
        for bone in armatureBones:
            if bone.hide: continue

            if armatureBoneCollections and bone.collections:
                if solo_BoneCollections:
                    if not any(col in solo_BoneCollections for col in bone.collections): continue
                else:
                    if not any(col.is_visible for col in bone.collections): continue

            visibleBones.append(bone.name)

    if bone_type == 'POSEBONE': return [armature.pose.bones.get(b) for b in visibleBones]
    if bone_type == 'EDITBONE': return [armature.data.edit_bones.get(b) for b in visibleBones]
    else: return [armature.data.bones.get(b) for b in visibleBones]


@selfreport
def apply_current_pose_as_restpose(armature: Object | None, only_selected : bool = False):
    if armature is None: return
    
    with unhide_all_objects(), preserve_armature_state(armature, reset_pose=False):
        try:
            mesh_objs = get_armature_meshes(armature)
            selected_objects = bpy.context.selected_objects
            active_object = bpy.context.view_layer.objects.active
            
            objects_to_transform = set()
            objects_to_transform.add(armature)
            
            for ob in armature.children:
                if ob.type not in {"EMPTY", "CURVE"}:
                    objects_to_transform.add(ob)
            
            for mesh_obj in mesh_objs:
                objects_to_transform.add(mesh_obj)
            
            empty_snapshot = {}
            for obj in armature.children:
                if obj.type == 'EMPTY' and obj.parent_type == 'BONE':
                    empty_snapshot[obj.name] = {
                        'location': obj.matrix_world.to_translation().copy(),
                        'rotation_matrix': obj.matrix_world.to_3x3().copy(),
                        'scale': obj.matrix_world.to_scale().copy()
                    }
            
            bpy.ops.object.select_all(action='DESELECT')
            for ob in objects_to_transform:
                try:
                    ob.select_set(True)
                except RuntimeError:
                    continue

            bpy.ops.object.transform_apply(location=True, scale=True, rotation=True)
            bpy.ops.object.mode_set(mode='POSE')

            for mesh_obj in mesh_objs:
                me = mesh_obj.data
                if not me:
                    continue

                if me.shape_keys and me.shape_keys.key_blocks:
                    key_blocks = me.shape_keys.key_blocks
                    if len(key_blocks) == 1:
                        original_basis_name = key_blocks[0].name
                        mesh_obj.shape_key_remove(key_blocks[0])
                        apply_armature_to_mesh_without_shape_keys(armature, mesh_obj)
                        mesh_obj.shape_key_add(name=original_basis_name)
                    else:
                        apply_armature_to_mesh_with_shapekeys(armature, mesh_obj, bpy.context)
                else:
                    apply_armature_to_mesh_without_shape_keys(armature, mesh_obj)

            op_override(bpy.ops.pose.armature_apply, {'active_object': armature})
            bpy.ops.object.mode_set(mode='OBJECT')

            fixed_count = reevaluate_bone_parented_empty_matrix(
                armature=armature,
                preserve_rotation=True,
                pre_transform_snapshot=empty_snapshot
            )
            if fixed_count > 0:
                report('INFO', f"Fixed {fixed_count} empty object(s)")

            bpy.ops.object.select_all(action='DESELECT')
            for obj in selected_objects:
                try:
                    obj.select_set(True)
                except RuntimeError:
                    continue
            bpy.context.view_layer.objects.active = active_object

        except Exception as e:
            report('ERROR', 'Failed to apply armature pose: {}'.format(str(e)))

        finally:
            bpy.context.view_layer.update()
            bpy.context.view_layer.depsgraph.update()


@selfreport
def apply_current_pose_shapekey(armature: Object | None, shapekey_name : str = ""):
    if not is_armature(armature): return

    with unhide_all_objects(), preserve_armature_state(armature, reset_pose=False):
        meshes = get_armature_meshes(armature)
        if not meshes: return
        
        success_count = 0

        # Evaluate constraints/drivers/parenting before measuring what actually moved.
        bpy.context.view_layer.update()

        # A bone counts as "posed" when its evaluated pose matrix differs from its rest
        # matrix. Using the evaluated matrix (not matrix_basis) catches bones moved only by
        # a constraint, and children that inherit motion from a driven/constrained parent -
        # neither of which touches matrix_basis.
        identity = Matrix.Identity(4)
        posebones = set()
        for pbone in armature.pose.bones:
            delta = pbone.bone.matrix_local.inverted_safe() @ pbone.matrix
            if sum(abs(val) for row in (delta - identity) for val in row) > 1e-4:
                posebones.add(pbone.name)

        bpy.ops.object.select_all(action='DESELECT')

        for mesh in meshes:
            arm_mod = next((mod for mod in mesh.modifiers if mod.type == 'ARMATURE' and mod.object == armature), None)
            
            if not arm_mod:
                report('WARNING', f"Mesh {mesh.name} has no Armature modifier for {armature.name}")
                continue
            
            used_vgroup_names = get_used_vertexgroups(mesh, return_names=True)

            # If none of the posed bones weight this mesh it cannot deform, so skip it
            # entirely instead of applying the modifier and adding an empty shapekey.
            if posebones.isdisjoint(used_vgroup_names):
                continue

            original_shapekey_values = {}
            if mesh.data.shape_keys and mesh.data.shape_keys.key_blocks:
                for sk in mesh.data.shape_keys.key_blocks:
                    original_shapekey_values[sk.name] = sk.value
                    sk.value = 0

            try:
                mesh.select_set(True)
                context_override = {'object': mesh, 'active_object': mesh, 'selected_objects': [mesh]}
                
                ret = op_override(bpy.ops.object.modifier_apply_as_shapekey, context_override, keep_modifier=True, modifier=arm_mod.name)
                
                if 'FINISHED' in ret:
                    if mesh.data.shape_keys:
                        new_key = mesh.data.shape_keys.key_blocks[-1]
                        
                        # Check if shapekey has any actual deformation
                        basis = mesh.data.shape_keys.key_blocks[0]
                        has_deformation = any(
                            (new_key.data[i].co - basis.data[i].co).length > 1e-4
                            for i in range(len(new_key.data))
                        )
                        
                        if not has_deformation:
                            mesh.shape_key_remove(new_key)
                            if len(mesh.data.shape_keys.key_blocks) <= 1:
                                mesh.shape_key_remove(mesh.data.shape_keys.key_blocks[0])
                        else:
                            success_count += 1
                            new_key.name = shapekey_name if shapekey_name else 'Pose_Shape'
                        
                else:
                    report('ERROR', f"Failed to apply modifier for {mesh.name}")

                mesh.select_set(False)
                
            except Exception as e:
                report('ERROR', f"Error processing {mesh.name}: {str(e)}")
                mesh.select_set(False)
                
            finally:
                for sk_name, sk_value in original_shapekey_values.items():
                    if sk_name in mesh.data.shape_keys.key_blocks:
                        mesh.data.shape_keys.key_blocks[sk_name].value = sk_value


def copy_armature_visual_pose(base_armature: Object, target_armature: Object, copy_type='ANGLES'):
    from .utils_bone import get_bone_exportname

    if not is_armature(base_armature) or not is_armature(target_armature):
        return
    
    base_bones = {get_bone_exportname(b, for_write=True): b for b in base_armature.data.bones}
    base_bones.update({b.name: b for b in base_armature.data.bones})
    
    target_bones = sort_bones_by_hierarchy(target_armature.data.bones)

    # Based on copy_attributes from Blender Foundation
    # SPDX-License-Identifier: GPL-3.0-or-later
    def getmat(bone, active, ignoreparent):
        """Helper function for visual transform copy, gets the active transform in bone space"""
        obj_bone = bone.id_data
        obj_active = active.id_data
        data_bone = obj_bone.data.bones[bone.name]
        
        active_to_selected = obj_bone.matrix_world.inverted() @ obj_active.matrix_world
        active_matrix = active_to_selected @ active.matrix
        otherloc = active_matrix
        bonemat_local = data_bone.matrix_local.copy()
        
        if data_bone.parent:
            parentposemat = obj_bone.pose.bones[data_bone.parent.name].matrix.copy()
            parentbonemat = data_bone.parent.matrix_local.copy()
        else:
            parentposemat = parentbonemat = Matrix()
            
        if parentbonemat == parentposemat or ignoreparent:
            newmat = bonemat_local.inverted() @ otherloc
        else:
            bonemat = parentbonemat.inverted() @ bonemat_local
            newmat = bonemat.inverted() @ parentposemat.inverted() @ otherloc
        return newmat

    def rotcopy(item, mat):
        """Copy rotation to item from matrix mat depending on item.rotation_mode"""
        if item.rotation_mode == 'QUATERNION':
            item.rotation_quaternion = mat.to_3x3().to_quaternion()
        elif item.rotation_mode == 'AXIS_ANGLE':
            rot = mat.to_3x3().to_quaternion().to_axis_angle()
            axis_angle = rot[1], rot[0][0], rot[0][1], rot[0][2]
            item.rotation_axis_angle = axis_angle
        else:
            item.rotation_euler = mat.to_3x3().to_euler(item.rotation_mode)

    with preserve_context_mode(base_armature, "POSE"), preserve_armature_state(base_armature, target_armature, reset_pose=False, reset_action=False):
        for target_data_bone in target_bones:
            export_name = get_bone_exportname(target_data_bone, for_write=True)
            base_data_bone = base_bones.get(export_name) or base_bones.get(target_data_bone.name)
            if not base_data_bone:
                continue

            target_pose_bone = target_armature.pose.bones[target_data_bone.name]
            base_pose_bone = base_armature.pose.bones[base_data_bone.name]
            
            if copy_type == 'ORIGIN':
                mat = getmat(target_pose_bone, base_pose_bone, False)
                target_pose_bone.location = mat.to_translation()
            elif copy_type == 'SCALE':
                ignoreparent = target_data_bone.inherit_scale not in {'NONE', 'NONE_LEGACY'}
                mat = getmat(target_pose_bone, base_pose_bone, ignoreparent)
                target_pose_bone.scale = mat.to_scale()
            else:
                ignoreparent = not target_data_bone.use_inherit_rotation
                mat = getmat(target_pose_bone, base_pose_bone, ignoreparent)
                rotcopy(target_pose_bone, mat)
            
            bpy.context.view_layer.update()


def fit_armature_pose_to_reference(source_arm: Object, ref_arm: Object, *, strength: float = 0.85,
                                   use_translate: bool = True, use_rotate: bool = True,
                                   use_scale: bool = True, scale_smoothing: float = 0.5,
                                   iterations: int = 3, tolerance: float = 0.001,
                                   selected_only: bool = False) -> tuple[int, int]:
    """Approximately pose ``source_arm`` so its matched joints align toward ``ref_arm``.

    Proportion-preserving, position-first fit. Bones are matched by name or export name. The goal
    is to land each joint (bone head) on the matching reference joint while keeping the source mesh
    proportions, so the result is "close enough" for deformation without skinny/squashed limbs. The
    source pose is refined in place (POSE mode) and left as a live pose; nothing is baked.

    Per bone (parents first, repeated ``iterations`` times):
      - ``use_translate``: move the bone head onto the reference joint (rigid, non-distorting).
      - ``use_rotate``:    aim the bone from its head toward its matched CHILD's target joint
                           (falls back to the reference bone's own direction if it has no matched child).
      - ``use_scale``:     UNIFORMLY scale each bone by the ratio of its rest joint-span to the target
                           joint-span, so a limb that must get longer also gets proportionally wider
                           (width follows length) instead of going skinny. Scale is precomputed from
                           fixed rest/target spans (so it never compounds or collapses to 1) and then
                           ``scale_smoothing`` (0..1) blends each bone's scale toward its parent's so a
                           short proximal bone doesn't balloon relative to its children (consistent
                           per-chain width). 0 = independent per bone, 1 = whole chain shares one scale.

    All channels are blended by ``strength`` (0..1). Returns ``(bones_moved, bones_matched)``.
    """
    from .utils_bone import get_bone_exportname

    if not is_armature(source_arm) or not is_armature(ref_arm):
        return (0, 0)

    strength = max(0.0, min(1.0, strength))
    if strength <= 0.0 or not (use_translate or use_rotate or use_scale):
        return (0, 0)

    # Reference lookup keyed by both export name and bone name.
    ref_map = {get_bone_exportname(b, for_write=True): b for b in ref_arm.data.bones}
    ref_map.update({b.name: b for b in ref_arm.data.bones})

    selected_names = None
    if selected_only:
        selected_names = {b.name for b in (get_selected_bones(source_arm, bone_type='POSEBONE') or [])}

    # Matched source->ref data bones, sources ordered parents-first.
    matched: dict[Bone, Bone] = {}
    ordered_src: list[Bone] = []
    for src_bone in sort_bones_by_hierarchy(list(source_arm.data.bones)):
        if selected_names is not None and src_bone.name not in selected_names:
            continue
        ref_bone = ref_map.get(src_bone.name) or ref_map.get(get_bone_exportname(src_bone, for_write=True))
        if ref_bone is not None:
            matched[src_bone] = ref_bone
            ordered_src.append(src_bone)

    if not ordered_src:
        return (0, 0)

    matched_set = set(matched)

    def nearest_matched_descendants(bone: Bone) -> list[Bone]:
        """Closest matched descendants (stop descending once a matched bone is reached)."""
        found: list[Bone] = []
        stack = list(bone.children)
        while stack:
            c = stack.pop()
            if c in matched_set:
                found.append(c)
            else:
                stack.extend(c.children)
        return found

    aim_children = {sb: nearest_matched_descendants(sb) for sb in ordered_src}
    # Fixed rest joint-span per bone (avg rest distance from its head to each matched child's head).
    rest_span: dict[Bone, float] = {}
    for sb in ordered_src:
        descs = aim_children[sb]
        rest_span[sb] = (sum((c.head_local - sb.head_local).length for c in descs) / len(descs)) if descs else 0.0

    EPS = 1e-6
    ANGLE_EPS = 1e-4
    moved_names: set[str] = set()

    # --- Precompute per-bone uniform scale, then smooth it along chains ---------------------
    # Spans are fixed (ref/source object transforms don't change while fitting), so compute once.
    m_ref2src0 = source_arm.matrix_world.inverted() @ ref_arm.matrix_world

    def _tgt_head(bone: Bone) -> 'Vector':
        return (m_ref2src0 @ ref_arm.pose.bones[matched[bone].name].matrix).translation

    raw_scale: dict[Bone, float | None] = {}
    for sb in ordered_src:
        descs = aim_children[sb]
        rs = rest_span[sb]
        if use_scale and descs and rs > EPS:
            h = _tgt_head(sb)
            tgt_span = sum((_tgt_head(c) - h).length for c in descs) / len(descs)
            r = 1.0 + ((tgt_span / rs) - 1.0) * strength
            raw_scale[sb] = max(0.05, min(20.0, r))
        else:
            raw_scale[sb] = None  # leaf / no span — inherit from parent

    def nearest_matched_ancestor(bone: Bone) -> 'Bone | None':
        p = bone.parent
        while p is not None:
            if p in matched_set:
                return p
            p = p.parent
        return None

    sm = max(0.0, min(1.0, scale_smoothing))
    smoothed_scale: dict[Bone, float] = {}
    for sb in ordered_src:  # parents-first, so the ancestor is already resolved
        anc = nearest_matched_ancestor(sb)
        parent_s = smoothed_scale.get(anc) if anc is not None else None
        own = raw_scale[sb]
        if own is None:
            smoothed_scale[sb] = parent_s if parent_s is not None else 1.0
        elif parent_s is None:
            smoothed_scale[sb] = own
        else:
            smoothed_scale[sb] = own * (1.0 - sm) + parent_s * sm

    with preserve_context_mode(source_arm, "POSE"):
        for _ in range(max(1, iterations)):
            m_ref2src = source_arm.matrix_world.inverted() @ ref_arm.matrix_world
            any_moved = False

            for src_bone in ordered_src:
                ref_bone = matched[src_bone]
                src_pb = source_arm.pose.bones[src_bone.name]
                ref_pb = ref_arm.pose.bones[ref_bone.name]

                S = src_pb.matrix.copy()
                T = m_ref2src @ ref_pb.matrix

                head_S = S.translation.copy()
                head_T = T.translation.copy()

                # --- Translate (head onto target joint) ---
                new_head = head_S.lerp(head_T, strength) if use_translate else head_S

                # --- Aim: point from new head toward the matched child's target joint ---
                children = aim_children.get(src_bone, [])
                child_tgts = [(m_ref2src @ ref_arm.pose.bones[matched[c].name].matrix).translation for c in children]

                if child_tgts:
                    aim_point = sum(child_tgts, Vector()) / len(child_tgts)
                    desired_dir = (aim_point - new_head)
                else:
                    desired_dir = (T.col[1].xyz)  # reference bone's own direction (its Y axis)

                rot = S.to_3x3()
                if use_rotate and desired_dir.length > EPS:
                    cur_dir = S.col[1].xyz
                    if cur_dir.length > EPS:
                        q_full = cur_dir.normalized().rotation_difference(desired_dir.normalized())
                        rot = Quaternion().slerp(q_full, strength).to_matrix() @ rot

                rot_n = rot.to_quaternion().to_matrix()

                # --- Uniform scale: precomputed (rest->target span) and smoothed along the chain
                # so width follows length without one short bone ballooning relative to its child.
                s = Vector((smoothed_scale[src_bone],) * 3) if use_scale else S.to_scale()

                # Early-out: already aligned this iteration.
                head_err = (head_T - head_S).length
                aim_err = (S.col[1].xyz).angle(desired_dir, 0.0) if desired_dir.length > EPS else 0.0
                if head_err < tolerance and aim_err < ANGLE_EPS:
                    continue

                new_S = (Matrix.Translation(new_head)
                         @ rot_n.to_4x4()
                         @ Matrix.Diagonal(s.to_4d()))
                src_pb.matrix = new_S
                bpy.context.view_layer.update()
                any_moved = True
                moved_names.add(src_bone.name)

            if not any_moved:
                break

    return (len(moved_names), len(ordered_src))


def merge_armatures( source_arm: Object, target_arm: Object, match_posture: bool = True, anchor_bone: str = "",
                    apply_pose: bool = True, group_bone_collections: bool = False):
    
    from .utils_bone import get_bone_exportname

    if not source_arm or not target_arm:
        return
    if source_arm.type != 'ARMATURE' or target_arm.type != 'ARMATURE':
        return

    print(f"Merging '{target_arm.name}' into '{source_arm.name}'...")

    with unhide_all_objects(), preserve_armature_state(source_arm, reset_pose=True), preserve_armature_state(target_arm, reset_pose=False):
        try:
            target_arm_name = target_arm.name
            target_meshes = get_armature_meshes(target_arm)
            print(f"  Found {len(target_meshes)} mesh(es) attached to target armature")

            if match_posture:
                try:
                    copy_armature_visual_pose(source_arm, target_arm, 'ANGLES')
                    copy_armature_visual_pose(source_arm, target_arm, 'ORIGIN')
                except Exception as e:
                    print(f"  Error matching posture for '{target_arm.name}': {e}")
                    return
                print("  Matched target posture to source")

            if apply_pose:
                apply_current_pose_as_restpose(target_arm)
                print(f"  Applied pose for '{target_arm.name}' as rest pose")

            source_bone_names = {b.name for b in source_arm.data.bones}
            source_export_map = {
                get_bone_exportname(b): b.name
                for b in source_arm.data.bones
                if get_bone_exportname(b)
            }

            target_root_bones = {b.name for b in target_arm.data.bones if not b.parent}

            bone_name_map = {}
            target_bones_without_collection = set()
            renamed_count = 0
            for target_bone in target_arm.data.bones:
                orig_name = target_bone.name

                if orig_name in source_bone_names:
                    target_bone.name = orig_name + ".temp_merge"
                    bone_name_map[target_bone.name] = orig_name
                    renamed_count += 1
                    continue
                
                target_export = get_bone_exportname(target_bone)
                matched_source = source_export_map.get(target_export)
                if matched_source:
                    target_bone.name = matched_source + ".temp_merge"
                    bone_name_map[target_bone.name] = matched_source
                    renamed_count += 1
                    continue
                
                bone_name_map[orig_name] = orig_name
                if len(target_bone.collections) == 0:
                    target_bones_without_collection.add(orig_name)

            if renamed_count > 0:
                print(f"  Prepared {renamed_count} bone(s) for merging")

            stored_parents = {
                ob.name: {'parent_type': ob.parent_type, 'parent_bone': ob.parent_bone}
                for ob in target_arm.children
            }

            stored_constraints = [
                {
                    'owner': pb.name,
                    'constraint': con.name,
                    'subtarget': getattr(con, 'subtarget', None)
                }
                for pb in target_arm.pose.bones
                for con in pb.constraints
                if getattr(con, 'target', None) == target_arm
            ]

            mesh_arm_driver_targets = [
                t
                for ob in target_meshes if ob.animation_data
                for fc in ob.animation_data.drivers
                for var in fc.driver.variables
                for t in var.targets
                if t.id == target_arm
            ]

            source_collection_names_before = {c.name for c in source_arm.data.collections}

            bpy.ops.object.select_all(action='DESELECT')
            for ob in target_meshes:
                ob.select_set(True)
            bpy.ops.object.transform_apply(rotation=True, location=True, scale=True)

            bpy.ops.object.select_all(action='DESELECT')
            source_arm.select_set(True)
            target_arm.select_set(True)
            bpy.context.view_layer.objects.active = source_arm
            bpy.ops.object.join()
            print("  Joined armatures")

            bpy.ops.object.mode_set(mode='EDIT')

            bones_to_remove = set()
            for bone in source_arm.data.edit_bones:
                if ".temp_merge" not in bone.name:
                    continue
                orig_name = bone.name.removesuffix(".temp_merge")
                source_bone = source_arm.data.edit_bones.get(orig_name)
                if source_bone:
                    for child in bone.children:
                        child.parent = source_bone
                bones_to_remove.add(bone)

            for bone in bones_to_remove:
                source_arm.data.edit_bones.remove(bone)

            if bones_to_remove:
                print(f"  Merged {len(bones_to_remove)} duplicate bone(s)")

            # Fix stale data_paths left by removed .temp_merge bones
            if source_arm.animation_data:
                for fc in source_arm.animation_data.drivers:
                    if ".temp_merge" not in fc.data_path:
                        continue
                    # e.g. pose.bones["Left arm.001.temp_merge"].rotation_euler
                    new_path = re.sub(
                        r'pose\.bones\["([^"]+)\.temp_merge"\]',
                        lambda m: f'pose.bones["{m.group(1)}"]',
                        fc.data_path
                    )
                    fc.data_path = new_path

            if anchor_bone:
                anchor = source_arm.data.edit_bones.get(anchor_bone)
                if anchor:
                    anchored_count = 0
                    for orig_bone_name in target_root_bones:
                        final_name = next(
                            (src for temp, src in bone_name_map.items()
                             if src == orig_bone_name and temp.endswith(".temp_merge")),
                            orig_bone_name
                        )
                        edit_bone = (source_arm.data.edit_bones.get(final_name)
                                     or source_arm.data.edit_bones.get(orig_bone_name))
                        if edit_bone and edit_bone.parent is None and edit_bone != anchor:
                            edit_bone.parent = anchor
                            anchored_count += 1
                    if anchored_count > 0:
                        print(f"  Anchored {anchored_count} root bone(s) to '{anchor_bone}'")

            bpy.ops.object.mode_set(mode='OBJECT')

            for ob_name, info in stored_parents.items():
                ob = bpy.data.objects.get(ob_name)
                if not ob:
                    continue
                ob.parent = source_arm
                ob.parent_type = info['parent_type']
                if info['parent_type'] == 'BONE' and info['parent_bone']:
                    mapped = bone_name_map.get(info['parent_bone'], info['parent_bone'])
                    if mapped in source_arm.data.bones:
                        ob.parent_bone = mapped

            for con_info in stored_constraints:
                owner_name = bone_name_map.get(con_info['owner'], con_info['owner'])
                owner_bone = source_arm.pose.bones.get(owner_name)
                if not owner_bone:
                    continue
                con = owner_bone.constraints.get(con_info['constraint'])
                if not con:
                    continue
                con.target = source_arm
                if con_info['subtarget']:
                    mapped = bone_name_map.get(con_info['subtarget'], con_info['subtarget'])
                    if mapped in source_arm.data.bones:
                        con.subtarget = mapped

            for ob in target_meshes:
                for mod in ob.modifiers:
                    if mod.type == 'ARMATURE' and mod.object != source_arm:
                        mod.object = source_arm
                ob.parent = source_arm

            vg_cleaned = 0
            for mesh in target_meshes:
                for vg in mesh.vertex_groups:
                    if ".temp_merge" in vg.name:
                        vg.name = vg.name.removesuffix(".temp_merge")
                        vg_cleaned += 1
            if vg_cleaned > 0:
                print(f"  Cleaned {vg_cleaned} vertex group(s)")

            # Remap mesh drivers that pointed to the (now absorbed) target_arm
            for t in mesh_arm_driver_targets:
                t.id = source_arm

            # Fix drivers that came over from target_arm via join - their id is now None
            driver_fixes = 0
            if source_arm.animation_data:
                for fc in source_arm.animation_data.drivers:
                    for var in fc.driver.variables:
                        for t in var.targets:
                            if t.id is None:
                                t.id = source_arm
                                driver_fixes += 1
                            if t.bone_target and t.bone_target in bone_name_map:
                                t.bone_target = bone_name_map[t.bone_target]
                                driver_fixes += 1
            if driver_fixes or mesh_arm_driver_targets:
                print(f"  Fixed {driver_fixes} armature driver ref(s), {len(mesh_arm_driver_targets)} mesh driver ref(s)")

            if group_bone_collections:
                arm_data = source_arm.data
                parent_coll = arm_data.collections.new(target_arm_name)

                all_new_collections = [
                    c for c in arm_data.collections_all
                    if c.name not in source_collection_names_before
                    and c.name != target_arm_name
                ]
                new_coll_names = {c.name for c in all_new_collections}

                for coll in all_new_collections:
                    parent_is_new = coll.parent is not None and coll.parent.name in new_coll_names
                    if not parent_is_new:
                        coll.parent = parent_coll

                uncollected = 0
                for bone in arm_data.bones:
                    if bone.name in target_bones_without_collection:
                        parent_coll.assign(bone)
                        uncollected += 1

                print(f"  Grouped target bone collections under '{target_arm_name}'"
                      f" ({len(all_new_collections)} collection(s), {uncollected} uncollected bone(s))")

            print(f"Successfully merged '{target_arm_name}' into '{source_arm.name}'")

        except Exception as e:
            print(f"  Merge failed: {e}")

        finally:
            bpy.context.view_layer.update()
            bpy.context.view_layer.depsgraph.update()


def transfer_armature_bonedata(source_arm: bpy.types.Object, target_arms: list, bone_filter: set = None, data_mode: str = 'ALL', sync_bone_collections: bool = False):
    from .utils_bone import get_bone_exportname

    if not source_arm or not target_arms:
        return
    if source_arm.type != 'ARMATURE':
        return

    targets = [t for t in target_arms if t and t.type == 'ARMATURE' and t != source_arm]
    if not targets:
        return

    print(f"Transferring bone data from '{source_arm.name}' to {len(targets)} armature(s)...")

    def find_collection(collections, name):
        for col in collections:
            if col.name == name:
                return col
            found = find_collection(col.children, name)
            if found:
                return found
        return None

    def ensure_collection(arm_data, src_col):
        existing = find_collection(arm_data.collections, src_col.name)
        if existing:
            existing.is_visible = src_col.is_visible
            return existing
        if src_col.parent:
            parent = ensure_collection(arm_data, src_col.parent)
        new_col = arm_data.collections.new(src_col.name)
        new_col.is_visible = src_col.is_visible
        if src_col.parent:
            new_col.parent = parent
        return new_col

    def sync_collections(arm_data, src_collections):
        for src_col in src_collections:
            ensure_collection(arm_data, src_col)
            sync_collections(arm_data, src_col.children)

    with preserve_armature_state(source_arm, reset_pose=True), unhide_all_objects():
        source_export_map = {
            get_bone_exportname(b): b.name
            for b in source_arm.data.bones
            if get_bone_exportname(b)
        }

        for target_arm in targets:
            print(f"  Updating '{target_arm.name}'...")

            with preserve_armature_state(target_arm, reset_pose=True):
                shared = {}
                for target_bone in target_arm.data.bones:
                    if target_bone.name in source_arm.data.bones:
                        source_name = target_bone.name
                    else:
                        export_name = get_bone_exportname(target_bone)
                        source_name = source_export_map.get(export_name)

                    if not source_name:
                        continue
                    if bone_filter is not None and source_name not in bone_filter:
                        continue

                    shared[target_bone.name] = source_name

                if not shared:
                    print(f"    No shared bones found, skipping")
                    continue

                if sync_bone_collections:
                    sync_collections(target_arm.data, source_arm.data.collections)

                    for bone in target_arm.data.bones:
                        source_name = shared.get(bone.name)
                        if not source_name:
                            continue
                        src_b = source_arm.data.bones.get(source_name)
                        if not src_b:
                            continue
                        for col in list(bone.collections):
                            col.unassign(bone)
                        for src_col in src_b.collections:
                            dst_col = find_collection(target_arm.data.collections, src_col.name)
                            if dst_col:
                                dst_col.assign(bone)

                    # Remove synced collections that ended up empty
                    def collect_all(collections):
                        result = []
                        for col in collections:
                            result.append(col)
                            result.extend(collect_all(col.children))
                        return result

                    for col in reversed(collect_all(target_arm.data.collections)):
                        if not col.bones:
                            target_arm.data.collections.remove(col)

                if data_mode in ('ALL', 'TRANSFORMS'):
                    bpy.context.view_layer.objects.active = source_arm
                    bpy.ops.object.mode_set(mode='EDIT')
                    source_edit_bones = {
                        b.name: {
                            'head': b.head.copy(),
                            'tail': b.tail.copy(),
                            'roll': b.roll,
                            'envelope_distance': b.envelope_distance,
                            'envelope_weight': b.envelope_weight,
                            'head_radius': b.head_radius,
                            'tail_radius': b.tail_radius,
                            'bbone_segments': b.bbone_segments,
                            'bbone_x': b.bbone_x,
                            'bbone_z': b.bbone_z,
                            'use_connect': b.use_connect,
                            'use_local_location': b.use_local_location,
                            'use_inherit_rotation': b.use_inherit_rotation,
                            'inherit_scale': b.inherit_scale,
                            'parent': b.parent.name if b.parent else None,
                        }
                        for b in source_arm.data.edit_bones
                    }
                    bpy.ops.object.mode_set(mode='OBJECT')

                    bpy.context.view_layer.objects.active = target_arm
                    bpy.ops.object.mode_set(mode='EDIT')

                    for target_name, source_name in shared.items():
                        src = source_edit_bones.get(source_name)
                        dst = target_arm.data.edit_bones.get(target_name)
                        if not src or not dst:
                            continue
                        for attr, value in src.items():
                            if attr == 'parent':
                                continue
                            setattr(dst, attr, value)

                    for target_name, source_name in shared.items():
                        src = source_edit_bones.get(source_name)
                        dst = target_arm.data.edit_bones.get(target_name)
                        if not src or not dst:
                            continue
                        src_parent_name = src.get('parent')
                        if src_parent_name is None:
                            dst.parent = None
                            continue
                        resolved_parent = next(
                            (t for t, s in shared.items() if s == src_parent_name),
                            src_parent_name
                        )
                        parent_edit_bone = target_arm.data.edit_bones.get(resolved_parent)
                        if parent_edit_bone:
                            dst.parent = parent_edit_bone

                    bpy.ops.object.mode_set(mode='OBJECT')

                if data_mode in ('ALL', 'PROPERTIES'):
                    for target_name, source_name in shared.items():
                        src_pb = source_arm.pose.bones.get(source_name)
                        dst_pb = target_arm.pose.bones.get(target_name)
                        if src_pb and dst_pb:
                            copy_property_group(src_pb, dst_pb)
                            copy_addon_properties(src_pb, dst_pb)

                        src_b = source_arm.data.bones.get(source_name)
                        dst_b = target_arm.data.bones.get(target_name)
                        if src_b and dst_b:
                            copy_property_group(src_b, dst_b)
                            copy_addon_properties(src_b, dst_b)

                print(f"    Updated {len(shared)} shared bone(s)")

    print(f"Transfer complete")