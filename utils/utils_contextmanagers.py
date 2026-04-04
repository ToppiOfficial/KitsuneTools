import bpy, inspect
from bpy.props import PointerProperty
from functools import wraps
from contextlib import contextmanager

MODE_MAP = {
    "OBJECT": "OBJECT",
    "EDIT_ARMATURE": "EDIT",
    "POSE": "POSE",
    "EDIT_MESH": "EDIT",
    "SCULPT": "SCULPT",
    "VERTEX_PAINT": "VERTEX_PAINT",
    "PAINT_VERTEX": "VERTEX_PAINT",
    "PAINT_WEIGHT": "WEIGHT_PAINT",
    "WEIGHT_PAINT": "WEIGHT_PAINT",
    "PAINT_TEXTURE": "TEXTURE_PAINT",
    "TEXTURE_PAINT": "TEXTURE_PAINT"
}

EDIT_MODE_MAP = (
    'EDIT_MESH',
    'EDIT_ARMATURE',
    'EDIT_CURVE',
    'EDIT_SURFACE',
    'EDIT_METABALL',
    'EDIT_TEXT',
    'EDIT_LATTICE'
)

_undo_depth = 0

#
#   MODULES
#

def is_addon_enabled(module_name: str) -> bool:
    return any(module_name in key for key in bpy.context.preferences.addons.keys())


def make_pointer(prop_type):
        return PointerProperty(name='Kitsune Tools settings',type=prop_type)

#
#   CONTEXT & SCENE MANAGERS
#


@contextmanager
def _undo_guard():
    global _undo_depth
    ctx = bpy.context

    _undo_enabled = ctx.preferences.edit.use_global_undo
    was_in_edit = False
    active_obj = None

    if _undo_depth == 0:
        ctx.preferences.edit.use_global_undo = False
        was_in_edit = ctx.mode in EDIT_MODE_MAP
        active_obj = ctx.view_layer.objects.active

    _undo_depth += 1
    try:
        yield
    except Exception:
        _undo_depth = 0
        ctx.preferences.edit.use_global_undo = True
        raise
    finally:
        if _undo_depth > 0:
            _undo_depth -= 1
        if _undo_depth == 0:
            if was_in_edit and active_obj and active_obj.name in bpy.data.objects:
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.ed.undo_push(message="Kitsune Operation")
                    bpy.ops.object.mode_set(mode='EDIT')
                except RuntimeError:
                    bpy.ops.ed.undo_push(message="Kitsune Operation")
            else:
                bpy.ops.ed.undo_push(message="Kitsune Operation")
            ctx.preferences.edit.use_global_undo = _undo_enabled


@contextmanager
def preserve_armature_state(*armatures: bpy.types.Object, reset_pose=True, reset_action=True):
    """
    Temporarily reset one or multiple armatures, then restore them on exit.

    Example:
        with PreserveArmatureState(arm1, arm2, reset_pose=True):
            # both arm1 and arm2 are clean
            ...
        # <-- all states restored (if still existing)

    Notes:
        - Deleted armatures are skipped during restore.
        - Deleted bones or bone collections are skipped.
        - Renamed bones are NOT restored (they are treated as new bones).
    """

    with _undo_guard():
        states = {}
        
        for armature in armatures:
            if armature.type != 'ARMATURE':
                continue

            state = {
                "pose_position": armature.data.pose_position,
                "edit_mirror_x": getattr(armature.data, "use_mirror_x", False),
                "pose_mirror_x": getattr(armature.pose, "use_mirror_x", False),
                "action": armature.animation_data.action if armature.animation_data else None,
                "bones": {},
                "bone_collections": {},
                "pose_bones": {},
                "pose_was_reset": bool(reset_pose),
            }

            for bone in armature.data.bones:
                state["bones"][bone.name] = bone.hide
                bone.hide = False

            if armature.data.edit_bones:
                for eb in armature.data.edit_bones:
                    eb.hide = False

            for bcoll in getattr(armature.data, "collections", []):
                state["bone_collections"][bcoll.name] = {
                    "is_visible": bcoll.is_visible,
                    "is_solo": bcoll.is_solo,
                }
                bcoll.is_visible = True
                bcoll.is_solo = False

            if reset_pose:
                for pbone in armature.pose.bones:
                    state["pose_bones"][pbone.name] = {
                        "location": pbone.location.copy(),
                        "scale": pbone.scale.copy(),
                        "rotation_mode": pbone.rotation_mode,
                        "rotation": (pbone.rotation_quaternion.copy() if pbone.rotation_mode == 'QUATERNION' else 
                                    pbone.rotation_axis_angle[:] if pbone.rotation_mode == 'AXIS_ANGLE' else 
                                    pbone.rotation_euler.copy())
                    }
                    pbone.matrix_basis.identity()

            if hasattr(armature.data, "use_mirror_x"): armature.data.use_mirror_x = False
            if hasattr(armature.pose, "use_mirror_x"): armature.pose.use_mirror_x = False
            if armature.animation_data and reset_action: armature.animation_data.action = None

            states[armature.name] = state

        try:
            yield armatures
        finally:
            for armature_name, state in states.items():
                armature = bpy.data.objects.get(armature_name)
                if not armature: continue

                armature.data.pose_position = state["pose_position"]
                
                if "edit_mirror_x" in state: armature.data.use_mirror_x = state["edit_mirror_x"]
                if "pose_mirror_x" in state: armature.pose.use_mirror_x = state["pose_mirror_x"]

                if reset_action and state["action"]:
                    if not armature.animation_data: armature.animation_data_create()
                    armature.animation_data.action = state["action"]

                for bone_name, hidden in state["bones"].items():
                    bone = armature.data.bones.get(bone_name)
                    if bone: bone.hide = hidden

                for bcoll_name, values in state["bone_collections"].items():
                    bcoll = next((c for c in armature.data.collections if c.name == bcoll_name), None)
                    if bcoll:
                        bcoll.is_visible = values["is_visible"]
                        bcoll.is_solo = values["is_solo"]

                if state["pose_was_reset"]:
                    for name, v in state["pose_bones"].items():
                        pb = armature.pose.bones.get(name)
                        if not pb: continue
                        pb.location, pb.scale, pb.rotation_mode = v["location"], v["scale"], v["rotation_mode"]
                        if pb.rotation_mode == 'QUATERNION': pb.rotation_quaternion = v["rotation"]
                        elif pb.rotation_mode == 'AXIS_ANGLE': pb.rotation_axis_angle = v["rotation"]
                        else: pb.rotation_euler = v["rotation"]


@contextmanager
def unhide_all_objects():
    """
    Temporarily unhide all objects and collections in the view layer.
    Restores original visibility afterwards.
 
    Notes:
        - Only restores objects/collections that were hidden before.
        - Deleted objects/collections are skipped safely.
    """
    with _undo_guard():
        ctx = bpy.context
        view_layer = ctx.view_layer
        root_layer_coll = view_layer.layer_collection
 
        original_visibility = {}
        original_obj_visibility = {}
 
        def store_layer_collection_visibility(layer_coll, vis):
            vis[layer_coll] = {
                "exclude": layer_coll.exclude,
                "hide_viewport": layer_coll.hide_viewport,
                "collection_hide_viewport": layer_coll.collection.hide_viewport,
            }
            for child in layer_coll.children:
                store_layer_collection_visibility(child, vis)
 
        def restore_layer_collection_visibility(vis):
            for layer_coll, state in vis.items():
                if layer_coll:
                    layer_coll.exclude = state["exclude"]
                    layer_coll.hide_viewport = state["hide_viewport"]
                    layer_coll.collection.hide_viewport = state["collection_hide_viewport"]
 
        def unhide_all_layer_collections(layer_coll):
            layer_coll.exclude = False
            layer_coll.hide_viewport = False
            layer_coll.collection.hide_viewport = False
            for child in layer_coll.children:
                unhide_all_layer_collections(child)
 
        store_layer_collection_visibility(root_layer_coll, original_visibility)
 
        for obj in bpy.data.objects:
            original_obj_visibility[obj.name] = {
                "hide": obj.hide_get(),
                "hide_viewport": obj.hide_viewport,
            }
            obj.hide_set(False)
            obj.hide_viewport = False
 
        unhide_all_layer_collections(root_layer_coll)
 
        try:
            yield
        finally:
            restore_layer_collection_visibility(original_visibility)
            for name, state in original_obj_visibility.items():
                if name not in bpy.data.objects:
                    continue
                obj = bpy.data.objects[name]
                obj.hide_set(state["hide"])
                obj.hide_viewport = state["hide_viewport"]


@contextmanager
def preserve_context_mode(obj: bpy.types.Object | None = None, mode: str = "EDIT"):
    with _undo_guard():
        ctx = bpy.context
        view_layer = ctx.view_layer
 
        prev_selected = list(view_layer.objects.selected)
        prev_active = view_layer.objects.active
        prev_mode = ctx.mode
        prev_vgroup_index = None
        prev_bone_name = None
        prev_bone_mode = None
        prev_bone_selected = None
 
        target_obj = obj or prev_active
 
        if target_obj:
            if target_obj.type == "MESH":
                prev_vgroup_index = target_obj.vertex_groups.active_index
            elif target_obj.type == "ARMATURE":
                data = target_obj.data
                if prev_mode == "EDIT_ARMATURE" and data.edit_bones.active:
                    prev_bone_name = data.edit_bones.active.name
                    prev_bone_mode = "EDIT"
                    prev_bone_selected = data.edit_bones.active.select
                elif prev_mode == "POSE" and data.bones.active:
                    prev_bone_name = data.bones.active.name
                    prev_bone_mode = "POSE"
                    prev_bone_selected = target_obj.pose.bones[prev_bone_name].bone.select
 
        if target_obj and target_obj.name in bpy.data.objects:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
 
            view_layer.objects.active = target_obj
            target_obj.select_set(True)
 
            try:
                bpy.ops.object.mode_set(mode=mode)
            except RuntimeError:
                pass
 
        try:
            if mode == "EDIT" and target_obj and target_obj.type == "ARMATURE":
                yield target_obj.data.edit_bones
            elif mode == "POSE" and target_obj and target_obj.type == "ARMATURE":
                yield target_obj.pose.bones
            else:
                yield target_obj
        finally:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
 
            bpy.ops.object.select_all(action="DESELECT")
            for sel in prev_selected:
                try:
                    if sel and sel.name in bpy.data.objects and sel.name in view_layer.objects:
                        sel.select_set(True)
                except ReferenceError:
                    pass
 
            if prev_active:
                try:
                    if prev_active.name in bpy.data.objects and prev_active.name in view_layer.objects:
                        view_layer.objects.active = prev_active
                except ReferenceError:
                    pass
 
            mapped_mode = MODE_MAP.get(prev_mode, "OBJECT")
            try:
                bpy.ops.object.mode_set(mode=mapped_mode)
            except RuntimeError:
                if prev_active:
                    try:
                        if prev_active.type == "ARMATURE":
                            bpy.ops.object.mode_set(mode="POSE")
                        elif prev_active.type == "MESH":
                            bpy.ops.object.mode_set(mode="OBJECT")
                    except ReferenceError:
                        pass
 
            if prev_active:
                try:
                    if prev_active.type == "MESH" and prev_vgroup_index is not None:
                        if 0 <= prev_vgroup_index < len(prev_active.vertex_groups):
                            prev_active.vertex_groups.active_index = prev_vgroup_index
                    elif prev_active.type == "ARMATURE" and prev_bone_name and prev_bone_mode:
                        data = prev_active.data
                        if mapped_mode == "EDIT" and prev_bone_mode == "EDIT":
                            edit_bone = data.edit_bones.get(prev_bone_name)
                            if edit_bone:
                                data.edit_bones.active = edit_bone
                                edit_bone.select = prev_bone_selected
                        elif mapped_mode == "POSE" and prev_bone_mode == "POSE":
                            bone = data.bones.get(prev_bone_name)
                            if bone:
                                data.bones.active = bone
                                bone.select = prev_bone_selected
                except ReferenceError:
                    pass

#
#   SELF REPORT
#

_report_buffer = []
_nesting_level = 0

def report(level, message):
    _report_buffer.append((level, message))


def selfreport(func=None, debug=False):
    
    def _find_operator_in_stack():
        for frame_info in inspect.stack():
            frame_locals = frame_info.frame.f_locals
            if 'self' in frame_locals:
                obj = frame_locals['self']
                if isinstance(obj, bpy.types.Operator):
                    return obj
        return None
    
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            global _report_buffer, _nesting_level
            
            _nesting_level += 1
            is_outermost = (_nesting_level == 1)
            
            if is_outermost:
                _report_buffer.clear()
            
            operator = _find_operator_in_stack()
            if debug:
                print(f"DEBUG: Found operator: {operator}, nesting level: {_nesting_level}")
            
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                report('ERROR', f"Exception in {f.__name__}: {str(e)}")
                raise
            finally:
                _nesting_level -= 1
                
                if is_outermost:
                    if debug:
                        print(f"DEBUG: Buffer has {len(_report_buffer)} reports")
                    if operator:
                        for level, message in _report_buffer:
                            operator.report({level}, message)
                        _report_buffer.clear()
            
            return result
        
        return wrapper
    
    if func is None:
        return decorator
    else:
        return decorator(func)


def flush_reports(operator):
    for level, message in _report_buffer:
        operator.report({level}, message)
    _report_buffer.clear()