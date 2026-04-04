import bpy, mathutils
from bpy.types import Object, LayerCollection, Bone, EditBone, PoseBone, Modifier, Context
from typing import Optional, Any, Callable, Dict
import numpy as np

shape_types = ('MESH' , 'SURFACE', 'CURVE')

#
#   BOOL FUNCTIONS
#

def is_object_visible_in_viewlayer(ob: Object, layer_collection: LayerCollection) -> bool:
    """Check if object is visible in the view layer (not excluded from collections)."""
    
    def find_collection_in_layer(obj_collection, layer_col):
        if obj_collection.name == layer_col.collection.name:
            return layer_col
        
        for child in layer_col.children:
            result = find_collection_in_layer(obj_collection, child)
            if result:
                return result
        return None
    
    for collection in ob.users_collection:
        layer_col = find_collection_in_layer(collection, layer_collection)
        
        if layer_col and not layer_col.exclude and not layer_col.hide_viewport:
            return True
    
    return False

def is_armature(ob: Object) -> bool:
    return bool(ob is not None and ob.type == 'ARMATURE')

def is_mesh(ob: Object) -> bool:
    return bool(ob is not None and ob.type == 'MESH')

def has_selected_bones() -> bool:
    armature = bpy.context.active_object
    if not is_armature(armature): return False
    
    if bpy.context.mode in 'EDIT_ARMATURE': return (any([bone.select for bone in armature.data.edit_bones]))
    else: return any([bone.select for bone in armature.data.bones])

def has_shapes(ob, valid_only = True):
    return bool(ob.type in shape_types and ob.data.shape_keys and len(ob.data.shape_keys.key_blocks))

#
#   GET FUNCTIONS
#

def get_armature(ob: Object | Bone | EditBone | PoseBone | None = None) -> Object | None:
    if isinstance(ob, Object):
        if ob.type == 'ARMATURE':
            return ob
        
        arm = ob.find_armature()
        if arm:
            return arm
        
        parent = ob.parent
        while parent:
            if parent.type == 'ARMATURE':
                return parent
            parent = parent.parent
        
        return None

    elif isinstance(ob, Bone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.data.bones.get(ob.name) == ob:
                return o

    elif isinstance(ob, EditBone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.data.edit_bones.get(ob.name) == ob:
                return o

    elif isinstance(ob, PoseBone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.pose.bones.get(ob.name) == ob:
                return o

    else:
        ctx_obj = bpy.context.active_object
        if ctx_obj:
            return get_armature(ctx_obj)
        return None
    
def get_armature_meshes(arm: Object | None, visible_only: bool = False, viewlayer_only: bool = True, strict_visibility: bool = True) -> set[Object]:
    """
    Get meshes using the given armature.
    
    Args:
        arm: The armature object.
        visible_only: If True, filter out hidden objects.
        viewlayer_only: If True, only search in current view layer.
        strict_visibility: 
            - True: use ob.visible_get() (full scene visibility check).
            - False: use ob.hide_get() (manual object hide only).
    """
    if arm is None: 
        return set()
    
    if viewlayer_only:
        view_layer = bpy.context.view_layer
        valid_objects = set(view_layer.objects)
    else:
        valid_objects = set(bpy.data.objects)
    
    result = set()
    
    for ob in valid_objects:
        if ob.type != 'MESH':
            continue
        
        if not any(mod.type == 'ARMATURE' and mod.object == arm for mod in ob.modifiers):
            continue
        
        if visible_only:
            if viewlayer_only:
                layer_collection = view_layer.layer_collection
                if not is_object_visible_in_viewlayer(ob, layer_collection):
                    continue
            
            if strict_visibility:
                if not ob.visible_get():
                    continue
            else:
                if ob.hide_get():
                    continue
        
        result.add(ob)
    
    return result


#
#   MODIFIERS
#

def op_override(operator, context_override: dict[str, Any], context: Optional[Context] = None,
                execution_context: Optional[str] = None, undo: Optional[bool] = None, **operator_args) -> set[str]:
    """Call a Blender operator with a context override."""
    args = []
    if execution_context is not None:
        args.append(execution_context)
    if undo is not None:
        args.append(undo)

    if context is None:
        context = bpy.context
    with context.temp_override(**context_override):
        return operator(*args, **operator_args)

def apply_modifier(mod: Modifier, strict: bool = False, silent=False):
    """
    Apply a modifier safely.
    
    Args:
        mod: The Blender modifier to apply.
        strict: 
            - If True -> deny applying if the object has shapekeys.
            - If False -> advanced Cats-style handling (bake + restore).
    """
    ob: Object | None = mod.id_data
    if ob is None or ob.type != 'MESH':
        return False
    
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)

    name = mod.name
    m_type = mod.type

    # Strict mode: deny applying if shapekeys exist
    if strict and ob.data.shape_keys:
        if not silent: 
            print(f"- Skipping {name} ({m_type}) on {ob.name}: object has shapekeys (strict mode).")
        return False

    if not strict and ob.data.shape_keys:
        if not silent: 
            print(f"- Applying modifier {name} ({m_type}) with shapekeys on {ob.name}")

        # Backup shapekeys
        shape_keys = {sk.name: [v.co.copy() for v in sk.data] 
                      for sk in ob.data.shape_keys.key_blocks}

        # Remove all shapekeys but preserve final shape
        context_override = {'object': ob, 'active_object': ob}
        op_override(bpy.ops.object.shape_key_remove, context_override, all=True, apply_mix=True)

        while ob.modifiers[0] != mod:
            bpy.ops.object.modifier_move_up(modifier=mod.name)
        bpy.ops.object.modifier_apply(modifier=mod.name)

        # Restore shapekeys only if vertex count unchanged
        if all(len(coords) == len(ob.data.vertices) for coords in shape_keys.values()):
            for sk_name, coords in shape_keys.items():
                new_sk = ob.shape_key_add(name=sk_name, from_mix=False)
                for i, coord in enumerate(coords):
                    new_sk.data[i].co = coord
            if not silent: 
                print(f"- Successfully applied {name} ({m_type}) with shapekeys preserved.")
        else:
            if not silent: 
                print(f"- Modifier {name} changed topology, shapekeys could not be restored.")

        return True

    # No shapekeys — apply normally
    while ob.modifiers[0] != mod:
        bpy.ops.object.modifier_move_up(modifier=mod.name)
    bpy.ops.object.modifier_apply(modifier=mod.name)

    if name not in ob.modifiers:
        if not silent: 
            print(f"- Pre-Applied Modifier {name} ({m_type}) for Object '{ob.name}'")
        return True
    else:
        if not silent: 
            print(f"- Failed to apply {name} ({m_type}) for Object '{ob.name}'")
        return False

#  Original source: https://github.com/teamneoneko/Avatar-Toolkit
def apply_armature_to_mesh_without_shape_keys(armature_obj: Object, mesh_obj: Object) -> None:
    """Apply armature deformation to a mesh that has no shape keys."""
    armature_mod: Modifier = mesh_obj.modifiers.new('PoseToRest', 'ARMATURE')
    armature_mod.object = armature_obj

    mesh_obj.modifiers.move(mesh_obj.modifiers.find(armature_mod.name), 0)

    # Apply with context override
    with bpy.context.temp_override(object=mesh_obj):
        bpy.ops.object.modifier_apply(modifier=armature_mod.name)

#  Original source: https://github.com/teamneoneko/Avatar-Toolkit
def apply_armature_to_mesh_with_shapekeys(armature_obj: Object, mesh_obj: Object, context: Context) -> None:
    """Apply armature deformation to mesh with shape keys (optimized depsgraph reuse)."""
    old_active_index = mesh_obj.active_shape_key_index
    old_show_only = mesh_obj.show_only_shape_key
    mesh_obj.show_only_shape_key = True

    me = mesh_obj.data
    key_blocks = me.shape_keys.key_blocks

    # Backup vertex groups + mute flags
    shape_key_vertex_groups = [sk.vertex_group for sk in key_blocks]
    shape_key_mutes = [sk.mute for sk in key_blocks]
    for sk in key_blocks:
        sk.vertex_group = ''
        sk.mute = False

    # Temporarily disable all visible modifiers
    mods_to_restore = []
    for mod in mesh_obj.modifiers:
        if mod.show_viewport:
            mod.show_viewport = False
            mods_to_restore.append(mod)

    # Add temporary armature modifier
    armature_mod = mesh_obj.modifiers.new('PoseToRest', 'ARMATURE')
    armature_mod.object = armature_obj

    # Pre-allocate coordinate array
    co_length = len(me.vertices) * 3
    eval_cos_array = np.empty(co_length, dtype=np.single)

    depsgraph = None
    evaluated_mesh_obj = None

    def get_eval_cos_array():
        nonlocal depsgraph, evaluated_mesh_obj
        if depsgraph is None or evaluated_mesh_obj is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated_mesh_obj = mesh_obj.evaluated_get(depsgraph)
        else:
            depsgraph.update()
        evaluated_mesh_obj.data.vertices.foreach_get('co', eval_cos_array)
        return eval_cos_array

    # Bake each shapekey
    for i, sk in enumerate(key_blocks):
        mesh_obj.active_shape_key_index = i
        evaluated_cos = get_eval_cos_array()
        sk.data.foreach_set('co', evaluated_cos)
        if i == 0:  # Also update basis mesh
            mesh_obj.data.vertices.foreach_set('co', evaluated_cos)

    # Restore modifiers and cleanup
    for mod in mods_to_restore:
        mod.show_viewport = True
    mesh_obj.modifiers.remove(armature_mod)

    # Restore shapekey settings
    for sk, vg, mute in zip(me.shape_keys.key_blocks, shape_key_vertex_groups, shape_key_mutes):
        sk.vertex_group = vg
        sk.mute = mute

    mesh_obj.active_shape_key_index = old_active_index
    mesh_obj.show_only_shape_key = old_show_only
    
def reevaluate_bone_parented_empty_matrix(armature: Optional[Object] = None,filter_func: Optional[Callable[[Object], bool]] = None,
    preserve_rotation: bool = True, pre_transform_snapshot: Optional[Dict] = None) -> int:
    """
    Fixes bone-parented empty objects by re-parenting them to maintain correct world transforms.
    
    This function corrects empty objects that are parented to armature bones, ensuring their
    world-space position, rotation, and scale remain accurate after re-parenting. This is
    useful when bone transforms have changed or when empties need to be reattached to bones.
    
    Args:
        armature: The armature object whose children should be processed. If None, all objects
                 in the scene are checked.
        filter_func: Optional callback function that takes an object and returns True if it
                    should be processed. Use this to selectively fix specific empties.
        preserve_rotation: If True, maintains the empty's world rotation. If False, resets
                          rotation to (0, 0, 0) in local space.
        pre_transform_snapshot: Optional dictionary containing pre-recorded world transforms
                               in the format: {obj_name: {'location': Vector, 
                               'rotation_matrix': Matrix, 'scale': Vector}}. Use this when
                               you need to restore transforms from before an operation.
    
    Returns:
        The number of empty objects that were fixed.
    """
    
    fixed_count = 0
    
    objects_to_process = []
    if armature:
        objects_to_process = armature.children
    else:
        objects_to_process = bpy.data.objects
    
    for obj in objects_to_process:
        if obj.type != 'EMPTY':
            continue
        
        if filter_func and not filter_func(obj):
            continue
        
        if not obj.parent or obj.parent.type != 'ARMATURE' or obj.parent_type != 'BONE':
            continue
        
        arm = obj.parent
        bone_name = obj.parent_bone
        
        if bone_name not in arm.data.bones:
            continue
        
        if pre_transform_snapshot and obj.name in pre_transform_snapshot:
            world_location = pre_transform_snapshot[obj.name]['location']
            world_rotation_matrix = pre_transform_snapshot[obj.name]['rotation_matrix']
            world_scale = pre_transform_snapshot[obj.name]['scale']
        else:
            world_location = obj.matrix_world.to_translation()
            world_rotation_matrix = obj.matrix_world.to_3x3()
            world_scale = obj.matrix_world.to_scale()
        
        pose_bone = arm.pose.bones[bone_name]
        bone_tip_matrix = arm.matrix_world @ pose_bone.matrix @ mathutils.Matrix.Translation((0, pose_bone.length, 0))
        
        obj.parent = None
        obj.parent = arm
        obj.parent_type = 'BONE'
        obj.parent_bone = bone_name
        
        local_location = bone_tip_matrix.inverted() @ world_location
        obj.location = local_location
        obj.scale = world_scale
        
        if preserve_rotation:
            bone_tip_rotation = bone_tip_matrix.to_3x3()
            local_rotation_matrix = bone_tip_rotation.inverted() @ world_rotation_matrix
            obj.rotation_euler = tuple(
                round(angle, 6) if abs(angle) > 1e-6 else 0.0 
                for angle in local_rotation_matrix.to_euler()
            )
        else:
            obj.rotation_euler = (0, 0, 0)
        
        fixed_count += 1
    
    return fixed_count
