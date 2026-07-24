import bpy, mathutils
from bpy.types import Object, LayerCollection, Bone, EditBone, PoseBone, Modifier, Context
from typing import Optional, Any, Callable, Dict
import numpy as np

shape_types = ('MESH' , 'SURFACE', 'CURVE')

# Blender 5.0 moved the bone selection flags (select / select_head / select_tail)
# off Bone (armature data) and onto PoseBone, so selection is no longer synced
# across armature instances. These helpers read/write selection on both 4.5 and 5.0+.
_SELECT_ON_POSEBONE = bpy.app.version >= (5, 0, 0)

def is_bone_selected(pose_bone: PoseBone) -> bool:
    """Return whether a pose bone is selected, on Blender 4.5 and 5.0+."""
    return pose_bone.select if _SELECT_ON_POSEBONE else pose_bone.bone.select

def set_bone_selected(pose_bone: PoseBone, state: bool) -> None:
    """Set a pose bone's selection, on Blender 4.5 and 5.0+."""
    if _SELECT_ON_POSEBONE:
        pose_bone.select = state
    else:
        pose_bone.bone.select = state

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
    else: return any([is_bone_selected(pb) for pb in armature.pose.bones])

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


#
#   SAFE TRANSFORM APPLY
#

# Object types whose transform Blender's object.transform_apply can bake into data.
TRANSFORM_APPLY_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'LATTICE', 'ARMATURE', 'GPENCIL', 'GREASEPENCIL'}

# Constraints that read the target's origin matrix. These break when the origin moves, and can be
# safely redirected to an anchor empty placed at the old origin.
ORIGIN_READING_CONSTRAINT_TYPES = {
    'COPY_LOCATION', 'COPY_ROTATION', 'COPY_SCALE', 'COPY_TRANSFORMS',
    'CHILD_OF', 'TRACK_TO', 'DAMPED_TRACK', 'LOCKED_TRACK', 'STRETCH_TO',
    'IK', 'TRANSFORM', 'PIVOT', 'FLOOR', 'ACTION',
}

# Constraints that read the target's geometry/curve rather than its origin matrix. Apply Transform
# is visually neutral for geometry, so these never break and must NOT be redirected to an empty.
GEOMETRY_SAFE_CONSTRAINT_TYPES = {
    'SHRINKWRAP', 'CLAMP_TO', 'FOLLOW_PATH', 'SPLINE_IK', 'ARMATURE',
}

ANCHOR_COLLECTION_NAME = "KitsuneTools Apply Anchors"


def get_constraint_target_world(target: Object, subtarget: str = "") -> mathutils.Matrix:
    """World-space matrix a constraint actually targets (bone-aware)."""
    if subtarget and target.type == 'ARMATURE':
        pose_bone = target.pose.bones.get(subtarget)
        if pose_bone:
            return target.matrix_world @ pose_bone.matrix
    return target.matrix_world.copy()


def sort_objects_parents_first(objects) -> list[Object]:
    """Topologically order objects so a parent is always processed before its children."""
    obj_set = set(objects)
    ordered: list[Object] = []
    visited = set()

    def visit(ob: Object) -> None:
        if ob in visited:
            return
        visited.add(ob)
        if ob.parent in obj_set:
            visit(ob.parent)
        ordered.append(ob)

    for ob in objects:
        visit(ob)
    return ordered


def gather_transform_apply_context(applied_objs) -> Dict[str, list]:
    """
    Collect everything in the scene whose result depends on the origin space of `applied_objs`,
    so an Apply Transform can be compensated instead of breaking things.

    Only references that read an applied object's ORIGIN matrix break: constraints with an empty
    subtarget whose type is origin-reading. Bone/vertex subtargets and geometry-based constraints
    (Shrinkwrap, Clamp To, …) stay visually correct because Apply Transform is geometry-neutral,
    so they are ignored.

    Returns a dict with:
        children:        objects parented to an applied object (any parent type) that are NOT
                         themselves being applied -> restore their world matrix afterwards.
        origin_refs:     (owner, pose_bone|None, constraint, attr, target) for each origin-reading
                         constraint reference -> Child Of inverse re-solved, or redirected to an
                         anchor in experimental mode. `attr` is 'target' or 'pole_target'.
        warn_refs:       (owner_label, constraint_name, constraint_type, target_name) for origin
                         references of an unrecognised constraint type -> warn only.
        deformed_meshes: meshes deformed by an applied armature but NOT selected -> warn only.
    """
    applied_set = set(applied_objs)

    children: list[Object] = []
    origin_refs: list[tuple] = []
    warn_refs: list[tuple] = []
    deformed_meshes: list[Object] = []

    for obj in bpy.data.objects:
        if obj not in applied_set and obj.parent in applied_set:
            children.append(obj)

    def scan_constraints(owner: Object, pose_bone, constraints) -> None:
        owner_label = owner.name if pose_bone is None else f"{owner.name} → {pose_bone.name}"
        for con in constraints:
            for attr, sub_attr in (('target', 'subtarget'), ('pole_target', 'pole_subtarget')):
                target = getattr(con, attr, None)
                if target not in applied_set:
                    continue
                if getattr(con, sub_attr, "") or "":
                    continue  # bone/vertex subtarget -> geometry preserved, stays correct
                if con.type in GEOMETRY_SAFE_CONSTRAINT_TYPES:
                    continue
                if con.type in ORIGIN_READING_CONSTRAINT_TYPES:
                    origin_refs.append((owner, pose_bone, con, attr, target))
                else:
                    warn_refs.append((owner_label, con.name, con.type, target.name))

    for obj in bpy.data.objects:
        scan_constraints(obj, None, obj.constraints)
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                scan_constraints(obj, pb, pb.constraints)

    applied_armatures = {o for o in applied_set if o.type == 'ARMATURE'}
    if applied_armatures:
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or obj in applied_set:
                continue
            if any(mod.type == 'ARMATURE' and mod.object in applied_armatures for mod in obj.modifiers):
                deformed_meshes.append(obj)

    return {
        'children': children,
        'origin_refs': origin_refs,
        'warn_refs': warn_refs,
        'deformed_meshes': deformed_meshes,
    }


def find_transform_driver_targets(applied_objs) -> list[tuple]:
    """
    Find driver variable targets that read an applied object's OBJECT-level transform.

    Bone-channel reads are skipped (Apply Transform is visually neutral for bones). Returns a list
    of (driver_target, applied_obj, owner_label, driver_data_path); `driver_target` is the live
    DriverTarget whose `.id` can be re-pointed to an anchor in experimental mode.
    """
    applied_set = set(applied_objs)
    transform_words = ('location', 'rotation', 'scale', 'delta_',
                       'matrix_world', 'matrix_basis', 'matrix_local')
    results: list[tuple] = []

    def scan(owner_label: str, anim) -> None:
        if not anim:
            return
        for fcurve in anim.drivers:
            for var in fcurve.driver.variables:
                for tgt in var.targets:
                    tid = getattr(tgt, 'id', None)
                    if tid not in applied_set:
                        continue
                    if var.type == 'TRANSFORMS':
                        if getattr(tgt, 'bone_target', ""):
                            continue  # bone transform -> safe
                        results.append((tgt, tid, owner_label, fcurve.data_path))
                    elif var.type == 'SINGLE_PROP':
                        dp = getattr(tgt, 'data_path', "") or ""
                        if 'pose.bones' in dp or 'bones[' in dp:
                            continue  # bone channel -> safe
                        if any(w in dp for w in transform_words):
                            results.append((tgt, tid, owner_label, fcurve.data_path))

    for obj in bpy.data.objects:
        scan(obj.name, obj.animation_data)
        data = getattr(obj, 'data', None)
        if data is not None and hasattr(data, 'animation_data'):
            scan(f"{obj.name} (data)", data.animation_data)
        if obj.type == 'MESH' and obj.data.shape_keys:
            scan(f"{obj.name} (shape keys)", obj.data.shape_keys.animation_data)

    return results


def get_apply_anchor_collection() -> bpy.types.Collection:
    """Get (or create) the collection that holds Apply Transform anchor empties."""
    coll = bpy.data.collections.get(ANCHOR_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(ANCHOR_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(coll)
    return coll


def create_apply_anchor(obj: Object, world_matrix: mathutils.Matrix, collection: bpy.types.Collection) -> Object:
    """Create an unparented empty at `world_matrix` to stand in for `obj`'s old origin."""
    anchor = bpy.data.objects.new(f"{obj.name}_ApplyAnchor", None)
    anchor.empty_display_type = 'ARROWS'
    anchor.empty_display_size = 0.1
    collection.objects.link(anchor)
    anchor.matrix_world = world_matrix
    return anchor


def attach_apply_anchor(anchor: Object, obj: Object, world_matrix: mathutils.Matrix) -> None:
    """Parent `anchor` to `obj` keeping it at `world_matrix`, so it follows the object afterwards."""
    anchor.parent = obj
    anchor.matrix_parent_inverse = obj.matrix_world.inverted()
    anchor.matrix_basis = world_matrix
