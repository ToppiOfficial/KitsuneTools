import bpy
from mathutils import Matrix
from bpy.types import Object, Bone, PoseBone, EditBone
from .utils_object import get_armature_meshes, is_armature
from .utils_contextmanagers import selfreport, preserve_armature_state, preserve_context_mode, unhide_all_objects, report
from .utils_object import op_override, apply_armature_to_mesh_without_shape_keys, apply_armature_to_mesh_with_shapekeys, reevaluate_bone_parented_empty_matrix


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


def get_visible_bones(armature: bpy.types.Object | None, bone_type: str = 'BONE', sort_type: str | None = 'TO_LAST',
                      exclude_active: bool = False) -> list[bpy.types.Bone | bpy.types.PoseBone | bpy.types.EditBone | None]:
    
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
def apply_current_pose_as_restpose(armature: Object | None):
    if armature is None: return False
    
    with preserve_armature_state(armature, reset_pose=False):
        try:
            with unhide_all_objects():
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
    from .utils_object import get_used_vertexgroups

    if not is_armature(armature): return
    
    meshes = get_armature_meshes(armature)
    if not meshes: return
    
    success_count = 0
    posebones = set()

    for pbone in armature.pose.bones:
        # Subtracting identity from the matrix; a rest bone results in a zero matrix
        diff_matrix = pbone.matrix_basis - Matrix.Identity(4)
        
        # Sum the absolute values of all 16 slots in the 4x4 matrix
        total_diff = sum(abs(val) for row in diff_matrix for val in row)

        if total_diff > 1e-4:
            posebones.add(pbone.name)
    
    with preserve_context_mode(armature, 'OBJECT'), unhide_all_objects():
        bpy.ops.object.select_all(action='DESELECT')
        for mesh in meshes:
            arm_mod = next((mod for mod in mesh.modifiers if mod.type == 'ARMATURE' and mod.object == armature), None)
            
            if not arm_mod:
                report('WARNING', "Mesh {mesh.name} has no Armature modifier for {armature.name}")
                continue
            
            original_shapekey_values = {}
            used_vgroup_names = get_used_vertexgroups(mesh, return_names=True)
            
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

                        if posebones.isdisjoint(used_vgroup_names):
                            mesh.shape_key_remove(new_key)
                            
                            if not len(mesh.data.shape_keys.key_blocks) > 1:
                                mesh.shape_key_remove(mesh.data.shape_keys.key_blocks[0])
                        else:
                            success_count += 1
                            pose_name = shapekey_name if shapekey_name else 'Pose_Shape'
                            new_key.name = pose_name
                        
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

    # Based on copy_attributes.py from Blender Foundation
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
            else:
                ignoreparent = not target_data_bone.use_inherit_rotation
                mat = getmat(target_pose_bone, base_pose_bone, ignoreparent)
                rotcopy(target_pose_bone, mat)
            
            bpy.context.view_layer.update()


def merge_armatures(source_arm: bpy.types.Object, target_arm: bpy.types.Object, match_posture=True, anchor_bone: str = "", apply_pose : bool = True):
    from .utils_bone import get_bone_exportname

    if not source_arm or not target_arm: return
    if source_arm.type != 'ARMATURE' or target_arm.type != 'ARMATURE': return

    print(f"Merging '{target_arm.name}' into '{source_arm.name}'...")

    with preserve_armature_state(source_arm, target_arm, reset_pose=True), unhide_all_objects():
        try:
            target_meshes = get_armature_meshes(target_arm)
            print(f"  Found {len(target_meshes)} mesh(es) attached to target armature")

            if match_posture:
                try:
                    copy_armature_visual_pose(source_arm, target_arm, 'ANGLES')
                    copy_armature_visual_pose(source_arm, target_arm, 'ORIGIN')
                except Exception as e:
                    print('Error matching and applying posture for {} armature: {}'.format(target_arm.name, str(e)))
                    return
                
                print("  Matched target posture to source")

            if apply_pose:
                apply_current_pose_as_restpose(target_arm)
                print("  Applied pose as rest pose")

            source_bone_names = {b.name for b in source_arm.data.bones}
            source_export_map = {get_bone_exportname(b): b.name for b in source_arm.data.bones if get_bone_exportname(b)}

            target_root_bones = {b.name for b in target_arm.data.bones if not b.parent}

            bone_name_map = {}
            renamed_count = 0
            for target_bone in target_arm.data.bones:
                old_name = target_bone.name

                if old_name in source_bone_names:
                    target_bone.name += ".temp_merge"
                    bone_name_map[target_bone.name] = old_name
                    renamed_count += 1
                    continue

                target_export = get_bone_exportname(target_bone)
                matched_source_name = source_export_map.get(target_export)

                if matched_source_name:
                    new_name = matched_source_name + ".temp_merge"
                    target_bone.name = new_name
                    renamed_count += 1
                
                bone_name_map[target_bone.name] = old_name

            if renamed_count > 0:
                print(f"  Prepared {renamed_count} bone(s) for merging")

            stored_parents = {}
            for ob in target_arm.children:
                stored_parents[ob.name] = {
                    'parent_type': ob.parent_type,
                    'parent_bone': ob.parent_bone
                }

            stored_constraints = []
            for pb in target_arm.pose.bones:
                for con in pb.constraints:
                    if getattr(con, 'target', None) == target_arm:
                        stored_constraints.append({
                            'owner': pb.name,
                            'constraint': con.name,
                            'subtarget': getattr(con, 'subtarget', None)
                        })

            bpy.ops.object.select_all(action='DESELECT')
            for ob in target_meshes:
                if not ob.select_get():
                    ob.select_set(True)
                if not ob.visible_get():
                    ob.select_set(True)

            bpy.ops.object.transform_apply(rotation=True, location=True, scale=True)
            bpy.ops.object.select_all(action='DESELECT')

            source_arm.select_set(True)
            target_arm.select_set(True)
            bpy.context.view_layer.objects.active = source_arm
            bpy.ops.object.join()
            print("  Joined armatures")

            bpy.ops.object.mode_set(mode="EDIT")

            bones_to_remove = set()
            for bone in source_arm.data.edit_bones:
                if ".temp_merge" in bone.name:
                    orig_name = bone.name.removesuffix(".temp_merge")
                    source_bone = source_arm.data.edit_bones.get(orig_name)
                    if source_bone:
                        for child in bone.children:
                            child.parent = source_bone
                    bones_to_remove.add(bone)
            
            for bone in bones_to_remove:
                source_arm.data.edit_bones.remove(bone)

            if len(bones_to_remove) > 0:
                print(f"  Merged {len(bones_to_remove)} duplicate bone(s)")

            if anchor_bone:
                anchor = source_arm.data.edit_bones.get(anchor_bone)
                if anchor:
                    anchored_count = 0
                    for bone_name in target_root_bones:
                        resolved_name = next(
                            (v for k, v in bone_name_map.items() if v == bone_name and k.endswith(".temp_merge")),
                            bone_name
                        )
                        edit_bone = source_arm.data.edit_bones.get(resolved_name) or source_arm.data.edit_bones.get(bone_name)
                        if edit_bone and edit_bone.parent is None and edit_bone != anchor:
                            edit_bone.parent = anchor
                            anchored_count += 1
                    if anchored_count > 0:
                        print(f"  Anchored {anchored_count} root bone(s) to '{anchor_bone}'")

            bpy.ops.object.mode_set(mode="OBJECT")

            for ob_name, info in stored_parents.items():
                ob = bpy.data.objects.get(ob_name)
                if not ob:
                    continue
                ob.parent = source_arm
                ob.parent_type = info['parent_type']
                if info['parent_type'] == 'BONE' and info['parent_bone']:
                    mapped_bone = bone_name_map.get(info['parent_bone'])
                    if mapped_bone and mapped_bone in source_arm.data.bones:
                        ob.parent_bone = mapped_bone

            for con_info in stored_constraints:
                owner_name = bone_name_map.get(con_info['owner'])
                if not owner_name:
                    continue
                owner_bone = source_arm.pose.bones.get(owner_name)
                if not owner_bone:
                    continue
                con = owner_bone.constraints.get(con_info['constraint'])
                if not con:
                    continue
                con.target = source_arm
                if con_info['subtarget']:
                    mapped_subtarget = bone_name_map.get(con_info['subtarget'])
                    if mapped_subtarget and mapped_subtarget in source_arm.data.bones:
                        con.subtarget = mapped_subtarget

            for ob in target_meshes:
                for mod in ob.modifiers:
                    if mod.type == 'ARMATURE' and mod.object != source_arm:
                        mod.object = source_arm
                ob.parent = source_arm

            vg_cleaned = 0
            for mesh in target_meshes:
                if mesh.vertex_groups:
                    for vg in mesh.vertex_groups:
                        if ".temp_merge" in vg.name:
                            vg.name = vg.name.removesuffix(".temp_merge")
                            vg_cleaned += 1
            
            if vg_cleaned > 0:
                print(f"  Merged {vg_cleaned} vertex group(s)")

            print(f"Successfully merged '{target_arm.name}' into '{source_arm.name}'")
            return

        except Exception as e:
            error_msg = f"Merge failed: {str(e)}"
            print(error_msg)
            return

        finally:
            bpy.context.view_layer.update()
            bpy.context.view_layer.depsgraph.update()