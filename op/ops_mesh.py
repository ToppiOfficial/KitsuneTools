import bpy, bmesh, mathutils, collections, math
from bpy.types import Operator, Context, Object
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, FloatProperty, FloatVectorProperty
from bpy.props import EnumProperty, BoolProperty, FloatProperty, IntProperty
from ..utils.utils_object import is_mesh, has_shapes
import numpy as np
from ..utils.utils_mesh import (clean_unused_shapekeys, read_shapekey_deltas, solve_shapekey_bones,
                                assign_shapekey_bone_weights, write_shapekey_pose_action,
                                create_hair_shadow_mesh, get_hair_shadow_material)
from ..utils.utils_vertexgroup import remove_unused_vertexgroups
from ..utils.utils_contextmanagers import preserve_context_mode


image_channels = [
    ('GREY', 'BW', 'The image is a greyscale mask. (Only the red channel is used)'),
    ('R', 'Red', ''),
    ('G', 'Green', ''),
    ('B', 'Blue', ''),
    ('A', 'Alpha', ''),
]


class MESH_OT_CleanShapeKeys(Operator):
    bl_idname = 'kitsunetools.clean_shape_keys'
    bl_label = 'Clean Shape Keys'
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context : Context) -> bool:
        return bool(is_mesh(context.active_object) and has_shapes(context.active_object, valid_only=True))
    
    def execute(self, context : Context) -> set:
        objects = context.selected_objects
        
        if not objects:
            self.report({'WARNING'}, 'No objects are selected')
            return {'CANCELLED'}
        
        cleaned_objects = 0
        removed_shapekeys = 0
        
        for ob in objects:
            if ob.type != 'MESH': continue
            
            deleted_sk = clean_unused_shapekeys(ob)
            
            if deleted_sk:
                cleaned_objects += 1
                removed_shapekeys += len(deleted_sk)
                
        if cleaned_objects and removed_shapekeys:
            self.report({'INFO'}, f'{cleaned_objects} objects processed with {removed_shapekeys} shapekeys removed')
        else:
            self.report({'INFO'}, f'No shapekeys were removed')
            
        return {'FINISHED'}


class MESH_OT_SelectShapekeyVerts(Operator):
    bl_idname = 'kitsunetools.select_shapekey_vertices'
    bl_label = 'Select Shapekey Vertices'
    bl_options = {'REGISTER', 'UNDO'}

    select_type: EnumProperty(
        name="Selection Type",
        items=[
            ('ACTIVE', "Active Shapekey", "Use only the active shapekey"),
            ('ALL', "All Shapekeys", "Use all shapekeys except the first (basis)"),
        ],
        default='ALL'
    )

    select_inverse: BoolProperty(
        name="Select Inverse",
        default=False,
        description="Select vertices *not* affected by the shapekey(s)"
    )

    threshold: FloatProperty(
        name="Threshold",
        description="Minimum vertex delta to consider as affected by shapekey",
        default=0.01,
        min=0.001,
        max=1.0,
        precision=4
    )

    @classmethod
    def poll(cls, context : Context) -> bool:
        ob  = context.active_object
        return bool(is_mesh(ob) and ob.data.shape_keys and ob.mode == 'EDIT')

    def execute(self, context : Context) -> set:
        obj = context.active_object
        mesh : Mesh = obj.data # type: ignore
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()

        shapekeys = mesh.shape_keys.key_blocks
        basis = shapekeys[0]

        if self.select_type == 'ACTIVE':
            keyblocks = [obj.active_shape_key] if obj.active_shape_key != basis else []
        else:  # ALL
            keyblocks = [kb for kb in shapekeys[1:]]

        basis_coords = basis.data

        affected_indices = {
            i for kb in keyblocks
            for i, (v_basis, v_shape) in enumerate(zip(basis_coords, kb.data))
            if (v_basis.co - v_shape.co).length > self.threshold
        }

        inv = self.select_inverse
        for i, v in enumerate(bm.verts):
            v.select_set((i in affected_indices) != inv)  # XOR

        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type='VERT')
        return {'FINISHED'}


class MESH_OT_RemoveUnusedVertexGroups(Operator):
    bl_idname = "kitsunetools.remove_unused_vertexgroups"
    bl_label = "Clean Unused Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}
    
    respect_mirror : BoolProperty(name='Respect Mirror', default=True)
    weight_threshold : FloatProperty(name='Weight Threshold', default=0.001,min=0.0001,max=0.1,precision=4)
    
    @classmethod
    def poll(cls, context : Context) -> bool:
        return bool(context.selected_objects)
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)
    
    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        
        col.prop(self, 'respect_mirror')
        col.prop(self, 'weight_threshold', slider=True)
    
    def execute(self, context : Context) -> set:
        obs = context.selected_objects
        total_removed = 0

        for ob in obs:
            removed_vgroups = remove_unused_vertexgroups(ob, weight_limit=self.weight_threshold, respect_mirror=self.respect_mirror)
            total_removed += sum(len(vgs) for vgs in removed_vgroups.values())

        self.report({'INFO'}, f"Removed {total_removed} unused vertex groups.")
        return {'FINISHED'}


class faces_by_imagemask():
    image_mask : StringProperty(name="Image Mask", default="")
    
    image_channel : EnumProperty(name='Channel', items=image_channels)
    
    invert_image_mask : BoolProperty(
        name="Invert Image Mask",
        default=False)

    exclude_selected_faces: BoolProperty(
        name="Exclude Selected Faces",
        description="Don't delete faces that are currently selected in Edit Mode",
        default=True
    )
    
    
class MESH_OT_Delete_Faces_by_ImageMask(Operator, faces_by_imagemask):
    bl_idname= "kitsunetools.delete_face_by_image_mask"
    bl_label= "Delete Face by Image Mask"
    bl_options: set = {"REGISTER", "UNDO"}
    
    material_name : StringProperty(
        name="Material",
        description="Only process faces assigned to this material. If empty, process all.",
        default="",
    )
    
    tolerance : FloatProperty(name='Tolerance', default=0.01, soft_min=0.00001,soft_max=0.03, precision=5)

    @classmethod
    def poll(cls, context):
        if bpy.data.images is None: return False
        return context.mode in ['OBJECT', 'EDIT_MESH'] and is_mesh(context.active_object) and hasattr(context.active_object.data, 'uv_layers')
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)
    
    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        
        col.prop_search(self, "image_mask", bpy.data, "images")

        if context.active_object and context.active_object.data and hasattr(context.active_object.data, 'materials'):
             col.prop_search(self, "material_name", context.active_object.data, "materials")
        
        col.prop(self, "image_channel")
        col.prop(self, "invert_image_mask")

        if context.mode == 'EDIT_MESH':
            col.prop(self, "exclude_selected_faces")

        col.prop(self, "tolerance", slider=True)
    
    def execute(self, context) -> set:
        image = bpy.data.images.get(self.image_mask)
        if image is None:
            self.report({'WARNING'}, "Image not found")
            return {'CANCELLED'}

        is_editmode = (context.mode == 'EDIT_MESH')
        
        if is_editmode:
            objects_to_process = {context.edit_object}
        else:
            objects_to_process = {obj for obj in context.selected_objects if is_mesh(obj)}

        if not objects_to_process:
            self.report({'WARNING'}, "No suitable mesh selected")
            return {'CANCELLED'}

        pixels = list(image.pixels)
        img_width = image.size[0]
        img_height = image.size[1]
        channels = image.channels

        faces_deleted_total = 0

        for obj in objects_to_process:
            target_mat_index = -1
            if self.material_name:
                target_mat_index = obj.data.materials.find(self.material_name)
                if target_mat_index == -1:
                    self.report({'INFO'}, f"Material '{self.material_name}' not on '{obj.name}', skipping.")
                    continue
            
            if is_editmode:
                bm = bmesh.from_edit_mesh(obj.data)
            else:
                bm = bmesh.new()
                bm.from_mesh(obj.data)
            
            uv_layer = bm.loops.layers.uv.active
            if not uv_layer:
                self.report({'INFO'}, f"Object '{obj.name}' has no active UV layer, skipping.")
                if not is_editmode:
                    bm.free()
                continue
            
            bm.faces.ensure_lookup_table()

            faces_to_delete = []
            for face in bm.faces:
                if is_editmode and self.exclude_selected_faces and face.select:
                    continue

                if target_mat_index != -1 and face.material_index != target_mat_index:
                    continue
                
                avg_brightness = 0.0
                
                if not face.loops:
                    continue
                
                for loop in face.loops:
                    uv = loop[uv_layer].uv
                    
                    u = uv.x % 1.0
                    v = uv.y % 1.0
                    
                    px = int(u * (img_width - 1))
                    py = int(v * (img_height - 1))

                    px = max(0, min(img_width - 1, px))
                    py = max(0, min(img_height - 1, py))
                    
                    pix_index = (py * img_width + px) * channels
                    
                    brightness = 0.0
                    if pix_index + (channels - 1) < len(pixels):
                        if self.image_channel == 'GREY':
                            if channels >= 1:
                                brightness = pixels[pix_index] # Red channel is used for greyscale
                        else:
                            channel_map = {'R': 0, 'G': 1, 'B': 2, 'A': 3}
                            channel_index = channel_map.get(self.image_channel)
                            if channel_index is not None and channel_index < channels:
                                brightness = pixels[pix_index + channel_index]
                    
                    avg_brightness += brightness
                
                avg_brightness /= len(face.loops)
                
                should_delete = avg_brightness < self.tolerance
                if self.invert_image_mask:
                    should_delete = not should_delete

                if should_delete:
                    faces_to_delete.append(face)

            if faces_to_delete:
                faces_deleted_total += len(faces_to_delete)
                bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')

                if is_editmode:
                    bmesh.update_edit_mesh(obj.data)
                else:
                    bm.to_mesh(obj.data)
                    obj.data.update()
            
            if not is_editmode:
                bm.free()

        self.report({'INFO'}, f"Deleted {faces_deleted_total} faces.")
        return {'FINISHED'}


class MESH_OT_Select_Faces_by_ImageMask(Operator, faces_by_imagemask):
    bl_idname= "kitsunetools.select_faces_by_image_mask"
    bl_label= "Select Faces by Image Mask"
    bl_options: set = {"REGISTER", "UNDO"}

    min_white_threshold: IntProperty(
        name="Min White Threshold",
        description="Select faces where the average brightness is above this value (0-255)",
        default=175,
        min=0,
        max=255
    )
    
    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and is_mesh(context.active_object) and hasattr(context.active_object.data, 'uv_layers')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        
        col.prop_search(self, "image_mask", bpy.data, "images")
        
        col.prop(self, "image_channel")
        col.prop(self, "invert_image_mask")
        col.prop(self, "exclude_selected_faces")
        col.prop(self, "min_white_threshold", slider=True)

    def execute(self, context) -> set:
        image = bpy.data.images.get(self.image_mask)
        if image is None:
            self.report({'WARNING'}, "Image not found")
            return {'CANCELLED'}

        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        
        uv_layer = bm.loops.layers.uv.active
        if not uv_layer:
            self.report({'INFO'}, f"Object '{obj.name}' has no active UV layer, skipping.")
            return {'CANCELLED'}
        
        pixels = list(image.pixels)
        img_width = image.size[0]
        img_height = image.size[1]
        channels = image.channels
        
        bm.faces.ensure_lookup_table()
        
        faces_selected_total = 0
        
        for face in bm.faces:
            if self.exclude_selected_faces and face.select:
                continue
            
            avg_brightness = 0.0
            
            if not face.loops:
                continue
            
            for loop in face.loops:
                uv = loop[uv_layer].uv
                
                u = uv.x % 1.0
                v = uv.y % 1.0
                
                px = int(u * (img_width - 1))
                py = int(v * (img_height - 1))
                
                px = max(0, min(img_width - 1, px))
                py = max(0, min(img_height - 1, py))
                
                pix_index = (py * img_width + px) * channels
                
                brightness = 0.0
                if pix_index + (channels - 1) < len(pixels):
                    if self.image_channel == 'GREY':
                        if channels >= 1:
                            brightness = pixels[pix_index] # Red channel is used for greyscale
                    else:
                        channel_map = {'R': 0, 'G': 1, 'B': 2, 'A': 3}
                        channel_index = channel_map.get(self.image_channel)
                        if channel_index is not None and channel_index < channels:
                            brightness = pixels[pix_index + channel_index]
                
                avg_brightness += brightness
            
            avg_brightness /= len(face.loops)
            
            avg_brightness_int = int(avg_brightness * 255)
            
            should_select = avg_brightness_int > self.min_white_threshold
            if self.invert_image_mask:
                should_select = not should_select
                
            if should_select:
                face.select = True
                faces_selected_total += 1

        bmesh.update_edit_mesh(obj.data)
        
        self.report({'INFO'}, f"Selected {faces_selected_total} faces.")
        return {'FINISHED'}
    

class MESH_OT_SelectLinkedMergeDistance(Operator):
    # Uses the "mesh." namespace (not "kitsunetools.") so Blender's WM_keymap_guess_opname
    # maps it to the Mesh keymap, which makes the right-click "Assign Shortcut" option appear.
    bl_idname = "mesh.select_linked_merge_distance"
    bl_label = "Select Linked All (Merge Distance)"
    bl_description = (
        "Select all geometry linked to the current selection, exactly like Select Linked All, "
        "but also treats vertices close enough to be merged as if they were linked. "
        "Useful for selecting across coincident but disconnected geometry without actually merging vertices"
    )
    bl_options = {'REGISTER', 'UNDO'}

    delimit: EnumProperty(
        name="Delimit",
        description="Delimit selected regions",
        options={'ENUM_FLAG'},
        items=[
            ('NORMAL', "Normal", "Delimit by face directions"),
            ('MATERIAL', "Material", "Delimit by material"),
            ('SEAM', "Seam", "Delimit by edge seams"),
            ('SHARP', "Sharp", "Delimit by sharp edges"),
            ('UV', "UVs", "Delimit by UV coordinates"),
        ],
        default={'SEAM'},
    )

    merge_distance: FloatProperty(
        name="Merge Distance",
        description="Vertices closer together than this world-space distance are treated as linked",
        default=0.0001,
        min=0.0,
        soft_max=0.1,
        precision=6,
        subtype='DISTANCE',
    )

    @classmethod
    def poll(cls, context : Context) -> bool:
        return context.mode == 'EDIT_MESH' and is_mesh(context.active_object)

    def execute(self, context) -> set:
        objects = [ob for ob in context.objects_in_mode if ob.type == 'MESH']
        if not objects:
            objects = [context.edit_object]

        # Bridging selects individual vertices, so we must run in vertex select mode:
        # in edge/face mode a lone selected vertex marks no edge/face as selected, and
        # the next select_linked pass would ignore it. Restore the user's mode afterward.
        original_select_mode = tuple(context.tool_settings.mesh_select_mode)
        context.tool_settings.mesh_select_mode = (True, False, False)

        try:
            # Precompute, per object, the map of each vertex to its coincident neighbours.
            # Coordinates are taken in world space so the threshold matches scene units and
            # is unaffected by object scale. Geometry never changes here (only selection
            # flags), so vertex indices stay stable across the loop below.
            object_data = []
            for ob in objects:
                bm = bmesh.from_edit_mesh(ob.data)
                bm.verts.ensure_lookup_table()

                mw = ob.matrix_world
                world_co = [mw @ v.co for v in bm.verts]

                kd = mathutils.kdtree.KDTree(len(world_co))
                for i, co in enumerate(world_co):
                    if bm.verts[i].hide:
                        continue
                    kd.insert(co, i)
                kd.balance()

                coincident = collections.defaultdict(set)
                for i, co in enumerate(world_co):
                    if bm.verts[i].hide:
                        continue
                    for (_co, idx, _dist) in kd.find_range(co, self.merge_distance):
                        if idx != i:
                            coincident[i].add(idx)

                object_data.append((ob, coincident))

            bridged_total = 0

            # Alternate between Blender's own linked-flood and bridging coincident vertices
            # until the selection stops growing.
            for _ in range(10000):
                bpy.ops.mesh.select_linked(delimit=self.delimit)

                changed = False
                for ob, coincident in object_data:
                    if not coincident:
                        continue

                    bm = bmesh.from_edit_mesh(ob.data)
                    bm.verts.ensure_lookup_table()

                    to_select = set()
                    for i, v in enumerate(bm.verts):
                        if v.select:
                            for j in coincident.get(i, ()):
                                if not bm.verts[j].select:
                                    to_select.add(j)

                    if to_select:
                        for j in to_select:
                            bm.verts[j].select_set(True)
                        bm.select_flush(True)
                        bmesh.update_edit_mesh(ob.data)
                        bridged_total += len(to_select)
                        changed = True

                if not changed:
                    break
        finally:
            context.tool_settings.mesh_select_mode = original_select_mode

        self.report({'INFO'}, f"Bridged {bridged_total} coincident vertices")
        return {'FINISHED'}


class MESH_OT_transfer_topology_shapekeys(bpy.types.Operator):
    bl_idname = "kitsunetools.transfer_topology_shapekeys"
    bl_label = "Transfer Topology Shape Keys"
    bl_description = "Transfer vertex positions from selected objects to active object as shape keys"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and 
                context.active_object.type == 'MESH' and
                len(context.selected_objects) > 1)
    
    def check_topology_match(self, mesh1, mesh2):
        if len(mesh1.vertices) != len(mesh2.vertices):
            return False
        if len(mesh1.edges) != len(mesh2.edges):
            return False
        if len(mesh1.polygons) != len(mesh2.polygons):
            return False
        return True
    
    def extract_shape_name(self, source_name, active_name):
        min_len = min(len(source_name), len(active_name))
        
        for i in range(min_len):
            if source_name[i] != active_name[i]:
                suffix = source_name[i:]
                return suffix if suffix else source_name
        
        if len(source_name) > len(active_name):
            return source_name[min_len:]
        
        return source_name
    
    def execute(self, context) -> set:
        active_obj = context.active_object
        selected_objects = [obj for obj in context.selected_objects if obj != active_obj and obj.type == 'MESH']
        
        if not selected_objects:
            self.report({'WARNING'}, "No other mesh objects selected")
            return {'CANCELLED'}
        
        active_mesh = active_obj.data
        
        if not active_mesh.shape_keys:
            basis = active_obj.shape_key_add(name="Basis", from_mix=False)
            basis.interpolation = 'KEY_LINEAR'
        
        transferred_count = 0
        skipped_count = 0
        skipped_names = []
        
        for source_obj in selected_objects:
            source_mesh = source_obj.data
            
            if not self.check_topology_match(active_mesh, source_mesh):
                skipped_names.append(source_obj.name)
                skipped_count += 1
                continue
            
            shape_key_name = self.extract_shape_name(source_obj.name, active_obj.name)
            counter = 1
            original_name = shape_key_name
            while shape_key_name in active_mesh.shape_keys.key_blocks:
                shape_key_name = f"{original_name}.{counter:03d}"
                counter += 1
            
            new_shape_key = active_obj.shape_key_add(name=shape_key_name, from_mix=False)
            new_shape_key.interpolation = 'KEY_LINEAR'
            new_shape_key.value = 0.0
            
            for i, vert in enumerate(source_mesh.vertices):
                new_shape_key.data[i].co = vert.co
            
            transferred_count += 1
        
        if skipped_count > 0:
            skipped_list = ", ".join(skipped_names)
            self.report({'WARNING'}, f"Topology mismatch - skipped: {skipped_list}")
        
        if transferred_count > 0:
            self.report({'INFO'}, f"Transferred {transferred_count} shape key(s)")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No shape keys transferred")
            return {'CANCELLED'}
    

class MESH_OT_CleanDuplicateMaterials(Operator):
    bl_idname = "kitsunetools.clean_duplicate_materials"
    bl_label = "Clean Duplicate Materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context) -> set:
        selected_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        materials_remapped = 0

        for obj in selected_meshes:
            if not obj.data.materials:
                continue

            for slot in obj.material_slots:
                if not slot.material:
                    continue

                mat_name = slot.material.name
                parts = mat_name.rsplit('.', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    base_name = parts[0]
                    if base_name in bpy.data.materials:
                        slot.material = bpy.data.materials[base_name]
                        materials_remapped += 1

        self.report({'INFO'}, f"Remapped {materials_remapped} duplicate material(s)")
        return {'FINISHED'}


_SIDE_PAIRS = [
    ("_right", "_left"), ("_left", "_right"),
    ("_Right", "_Left"), ("_Left", "_Right"),
    ("_RIGHT", "_LEFT"), ("_LEFT", "_RIGHT"),
    (".right", ".left"), (".left", ".right"),
    (".Right", ".Left"), (".Left", ".Right"),
    (".RIGHT", ".LEFT"), (".LEFT", ".RIGHT"),
    ("_R", "_L"), ("_L", "_R"),
    (".R", ".L"), (".L", ".R"),
    ("_r", "_l"), ("_l", "_r"),
    (".r", ".l"), (".l", ".r"),
    ("right_", "left_"), ("left_", "right_"),
    ("Right_", "Left_"), ("Left_", "Right_"),
    ("RIGHT_", "LEFT_"), ("LEFT_", "RIGHT_"),
    ("R_", "L_"), ("L_", "R_"),
    ("r_", "l_"), ("l_", "r_"),
]


def _find_mirror_bone_name(name: str):
    """Return the opposite-side bone name for known side suffixes/prefixes, or None."""
    for src, dst in _SIDE_PAIRS:
        if name.endswith(src):
            return name[: -len(src)] + dst
        if name.startswith(src):
            return dst + name[len(src) :]
    return None


def _strip_side_name(name: str) -> str:
    """Return the name without its side suffix/prefix, matched the same way as _find_mirror_bone_name."""
    for src, _dst in _SIDE_PAIRS:
        if name.endswith(src):
            return name[: -len(src)]
        if name.startswith(src):
            return name[len(src) :]
    return name


class MESH_OT_convex_hull_selection(bpy.types.Operator):
    bl_idname = "kitsunetools.convex_hull_selection"
    bl_label = "Convex Hull from Selection"
    bl_description = "Separate selected faces into a new object and apply convex hull"
    bl_options = {'REGISTER', 'UNDO'}

    keep_original: bpy.props.BoolProperty(
        name="Keep Original",
        description="Duplicate the selection before separating, preserving the original mesh",
        default=True
    )
    delete_unused_verts: bpy.props.BoolProperty(
        name="Delete Unused",
        description="Delete vertices not used by the convex hull",
        default=True
    )
    use_existing_faces: bpy.props.BoolProperty(
        name="Use Existing Faces",
        description="Reuse existing faces within the hull",
        default=True
    )
    make_holes: bpy.props.BoolProperty(
        name="Make Holes",
        description="Leave holes in the original mesh where faces were removed",
        default=False
    )
    join_triangles: bpy.props.BoolProperty(
        name="Join Triangles",
        description="Merge adjacent triangles into quads",
        default=True
    )
    face_threshold: bpy.props.FloatProperty(
        name="Max Face Angle",
        description="Face angle threshold for joining triangles",
        default=0.698132,
        min=0.0,
        max=3.14159,
        subtype='ANGLE'
    )
    shape_threshold: bpy.props.FloatProperty(
        name="Max Shape Angle",
        description="Shape angle threshold for joining triangles",
        default=0.698132,
        min=0.0,
        max=3.14159,
        subtype='ANGLE'
    )
    decimation_factor: bpy.props.FloatProperty(
        name="Decimation Factor",
        description="Decimate the convex hull mesh. A value of 1.0 skips decimation",
        default=0.22,
        min=0.001,
        max=1.0
    )
    clean_modifiers: bpy.props.BoolProperty(
        name="Clean Modifiers",
        description="Remove all modifiers from the physics mesh after hull conversion",
        default=True
    )
    clean_vertex_groups: bpy.props.BoolProperty(
        name="Clean Vertex Groups",
        description="Remove all vertex groups from the physics mesh after hull conversion",
        default=True
    )
    rig_to_bone: bpy.props.BoolProperty(
        name="Rig to Single Bone",
        description="Assign all vertices to a single bone; names the mesh {bone}_physicsmesh",
        default=False
    )
    bone_name: bpy.props.StringProperty(
        name="Bone Name",
        description="Name of the bone to rig the physics mesh to",
        default=""
    )
    add_mirror_mod: bpy.props.BoolProperty(
        name="Add Mirror Modifier",
        description="Add a Mirror (X-axis) modifier to supplement symmetry rigging",
        default=True
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "keep_original")
        layout.prop(self, "delete_unused_verts")
        layout.prop(self, "use_existing_faces")
        layout.prop(self, "make_holes")
        layout.prop(self, "join_triangles")
        layout.prop(self, "face_threshold")
        layout.prop(self, "shape_threshold")
        layout.prop(self, "decimation_factor")
        layout.prop(self, "clean_modifiers")
        layout.prop(self, "clean_vertex_groups")
        layout.prop(self, "rig_to_bone")
        if self.rig_to_bone:
            armature_obj = None
            if context.active_object:
                for mod in context.active_object.modifiers:
                    if mod.type == 'ARMATURE' and mod.object:
                        armature_obj = mod.object
                        break
            if armature_obj:
                layout.prop_search(self, "bone_name", armature_obj.data, "bones")
            else:
                layout.prop(self, "bone_name")
            layout.prop(self, "add_mirror_mod")
            if self.bone_name:
                mirror = _find_mirror_bone_name(self.bone_name)
                valid = mirror is not None
                if valid and armature_obj:
                    valid = mirror in armature_obj.data.bones
                if valid:
                    layout.label(text=f"Mirror VG: {mirror}", icon='BONE_DATA')
                else:
                    layout.label(text="No valid mirror bone (non-mirror)", icon='INFO')

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def execute(self, context) -> set:
        active_obj = context.active_object
        edit_mode_objects = set(context.objects_in_mode)

        with preserve_context_mode(active_obj, 'EDIT'):
            if self.keep_original:
                bpy.ops.mesh.duplicate()

            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            separated_objs = [
                obj for obj in context.selected_objects
                if obj not in edit_mode_objects and obj.type == 'MESH'
            ]

            if not separated_objs:
                self.report({'WARNING'}, "No geometry was separated")
                return {'CANCELLED'}

            bpy.ops.object.select_all(action='DESELECT')
            for obj in separated_objs:
                obj.select_set(True)
            context.view_layer.objects.active = separated_objs[0]

            if len(separated_objs) > 1:
                bpy.ops.object.join()

            armature_obj = None
            mirror_bone = None
            has_valid_mirror = False
            if self.rig_to_bone and self.bone_name:
                for mod in active_obj.modifiers:
                    if mod.type == 'ARMATURE' and mod.object:
                        armature_obj = mod.object
                        break
                mirror_bone = _find_mirror_bone_name(self.bone_name)
                # If an armature is present the mirror bone must exist in it,
                # otherwise the name-based match is enough.
                has_valid_mirror = mirror_bone is not None
                if has_valid_mirror and armature_obj:
                    has_valid_mirror = mirror_bone in armature_obj.data.bones

            new_obj = context.active_object
            if self.rig_to_bone and self.bone_name:
                base_name = _strip_side_name(self.bone_name) if has_valid_mirror else self.bone_name
                new_obj.name = f"{base_name}_physicsmesh"
            else:
                new_obj.name = f"{active_obj.name}_physicsmesh"
            new_obj.data.materials.clear()

            if self.clean_modifiers:
                new_obj.modifiers.clear()
            if self.clean_vertex_groups:
                new_obj.vertex_groups.clear()

            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.convex_hull(
                delete_unused=self.delete_unused_verts,
                use_existing_faces=self.use_existing_faces,
                make_holes=self.make_holes,
                join_triangles=self.join_triangles,
                face_threshold=self.face_threshold,
                shape_threshold=self.shape_threshold
            )
            bpy.ops.object.mode_set(mode='OBJECT')

            if new_obj.data.shape_keys:
                new_obj.shape_key_clear()

            if self.decimation_factor < 1.0:
                mod = new_obj.modifiers.new(name="Decimate", type='DECIMATE')
                mod.ratio = self.decimation_factor
                bpy.ops.object.modifier_apply(modifier=mod.name)

                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.normals_tools(mode='RESET')
                bpy.ops.object.mode_set(mode='OBJECT')

            bpy.ops.object.shade_smooth()

            if self.rig_to_bone and self.bone_name:
                vg = new_obj.vertex_groups.new(name=self.bone_name)
                all_indices = [v.index for v in new_obj.data.vertices]
                vg.add(all_indices, 1.0, 'REPLACE')
                vg.lock_weight = True

                if has_valid_mirror:
                    vg_mirror = new_obj.vertex_groups.new(name=mirror_bone)
                    vg_mirror.lock_weight = True

                if armature_obj:
                    if self.add_mirror_mod and has_valid_mirror:
                        mir_mod = new_obj.modifiers.new(name="Mirror", type='MIRROR')
                        mir_mod.mirror_object = armature_obj
                    arm_mod = new_obj.modifiers.new(name="Armature", type='ARMATURE')
                    arm_mod.object = armature_obj
                    new_obj.parent = armature_obj
                    new_obj.parent_type = 'OBJECT'
                else:
                    if self.add_mirror_mod and has_valid_mirror:
                        new_obj.modifiers.new(name="Mirror", type='MIRROR')
                    self.report({'WARNING'}, "No armature found on source object; vertex group added but modifier/parent skipped")
            elif self.rig_to_bone:
                self.report({'WARNING'}, "Bone name is empty; rig setup skipped")

        self.report({'INFO'}, f"Convex hull created: {new_obj.name}")
        return {'FINISHED'}


class MESH_OT_replace_verts_with_spheres(bpy.types.Operator):
    bl_idname = "kitsunetools.replace_verts_with_spheres"
    bl_label = "Replace Vertices with Spheres"
    bl_options = {'REGISTER', 'UNDO'}

    sphere_radius: bpy.props.FloatProperty(name="Radius", default=0.2, min=0.001, max=10.0)
    segments: bpy.props.IntProperty(name="Segments", default=6, min=3, max=64)
    rings: bpy.props.IntProperty(name="Rings", default=6, min=3, max=64)

    weight_mode: bpy.props.EnumProperty(
        name="Weight Mode",
        items=[
            ('HIGHEST', "Highest", "Assign the vertex group with the highest total weight"),
            ('AVERAGE', "Average", "Distribute averaged weights across all vertex groups"),
        ],
        default='AVERAGE'
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context) -> set:
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object")
            return {'CANCELLED'}

        with preserve_context_mode(obj, 'EDIT'):
            bm = bmesh.from_edit_mesh(obj.data)

            uv_layer = bm.loops.layers.uv.active
            deform_layer = bm.verts.layers.deform.active

            selected_verts = [v for v in bm.verts if v.select]

            if not selected_verts:
                self.report({'WARNING'}, "No vertices selected")
                return {'CANCELLED'}

            overlapping_groups = collections.defaultdict(list)
            for v in selected_verts:
                coord_key = (round(v.co.x, 5), round(v.co.y, 5), round(v.co.z, 5))
                overlapping_groups[coord_key].append(v)

            groups_to_process = [
                (verts, mathutils.Vector(coord)) for coord, verts in overlapping_groups.items()
            ]

            # Collect dominant vertex group per position before removing verts
            group_data = {}
            if deform_layer:
                for verts, center in groups_to_process:
                    group_weight_totals = collections.defaultdict(float)
                    for v in verts:
                        for group_index, weight in v[deform_layer].items():
                            group_weight_totals[group_index] += weight
                    if group_weight_totals:
                        coord_key = (round(center.x, 5), round(center.y, 5), round(center.z, 5))
                        if self.weight_mode == 'HIGHEST':
                            group_data[coord_key] = max(group_weight_totals, key=group_weight_totals.get)  # pyright: ignore
                        else:
                            total = sum(group_weight_totals.values())
                            group_data[coord_key] = {idx: w / total for idx, w in group_weight_totals.items()}

            verts_to_remove = {v for verts, _ in groups_to_process for v in verts}

            sphere_vert_map = []  # list of (sphere_verts, center)
            for verts, center in groups_to_process:
                sphere_verts = self._create_sphere(bm, center, uv_layer)
                sphere_vert_map.append((sphere_verts, center))

            for v in verts_to_remove:
                if v.is_valid:
                    for face in list(v.link_faces):
                        if face.is_valid:
                            bm.faces.remove(face)

            for v in verts_to_remove:
                if v.is_valid:
                    bm.verts.remove(v)

            bm.verts.ensure_lookup_table()

            if deform_layer and group_data:
                for sphere_verts, center in sphere_vert_map:
                    coord_key = (round(center.x, 5), round(center.y, 5), round(center.z, 5))
                    data = group_data.get(coord_key)
                    if data is None:
                        continue
                    for v in sphere_verts:
                        if not v.is_valid:
                            continue
                        if self.weight_mode == 'HIGHEST':
                            v[deform_layer][data] = 1.0  # pyright: ignore
                        else:
                            for group_index, weight in data.items():
                                v[deform_layer][group_index] = weight  # pyright: ignore

            bm.normal_update()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            bm.verts.index_update()
            bm.edges.index_update()
            bm.faces.index_update()

            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)

            bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'}, f"Created {len(groups_to_process)} UV spheres")
        return {'FINISHED'}

    def _create_sphere(self, bm, center, uv_layer):
        segments = self.segments
        rings = self.rings
        radius = self.sphere_radius

        verts_grid = []
        all_verts = []
        for i in range(rings + 1):
            lat = math.pi * i / rings - math.pi / 2
            ring_verts = []
            for j in range(segments):
                lon = 2 * math.pi * j / segments
                pos = center + mathutils.Vector((
                    radius * math.cos(lat) * math.cos(lon),
                    radius * math.cos(lat) * math.sin(lon),
                    radius * math.sin(lat)
                ))
                v = bm.verts.new(pos)
                ring_verts.append(v)
                all_verts.append(v)
            verts_grid.append(ring_verts)

        for i in range(rings):
            for j in range(segments):
                j_next = (j + 1) % segments

                if i == 0:
                    face_verts = [verts_grid[i][j], verts_grid[i + 1][j], verts_grid[i + 1][j_next]]
                elif i == rings - 1:
                    face_verts = [verts_grid[i][j], verts_grid[i][j_next], verts_grid[i + 1][j]]
                else:
                    face_verts = [verts_grid[i][j], verts_grid[i + 1][j], verts_grid[i + 1][j_next], verts_grid[i][j_next]]

                try:
                    face = bm.faces.new(face_verts)
                except ValueError:
                    continue

                if not uv_layer:
                    continue

                angle1 = 2 * math.pi * j / segments
                angle2 = 2 * math.pi * (j + 1) / segments
                lat_top = math.pi * i / rings - math.pi / 2
                lat_bot = math.pi * (i + 1) / rings - math.pi / 2
                r_top = math.cos(lat_top) * 0.5
                r_bot = math.cos(lat_bot) * 0.5

                uvs = [
                    (0.5 + r_top * math.cos(angle1), 0.5 + r_top * math.sin(angle1)),
                    (0.5 + r_bot * math.cos(angle1), 0.5 + r_bot * math.sin(angle1)),
                    (0.5 + r_bot * math.cos(angle2), 0.5 + r_bot * math.sin(angle2)),
                ]
                if len(face_verts) == 4:
                    uvs.append((0.5 + r_top * math.cos(angle2), 0.5 + r_top * math.sin(angle2)))

                for loop, uv in zip(face.loops, uvs):
                    loop[uv_layer].uv = mathutils.Vector(uv)

        return all_verts


class MESH_OT_AlignViewToFaceNormals(bpy.types.Operator):
    bl_idname = "kitsunetools.align_view_to_face_normals"
    bl_label = "Align to Face Normals"
    bl_description = "Point the viewport to face the selected faces along their averaged normal"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
            and context.space_data is not None
            and context.space_data.type == 'VIEW_3D'
        )

    def execute(self, context) -> set:
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        selected = [f for f in bm.faces if f.select]
        if not selected:
            self.report({'WARNING'}, "No faces selected")
            return {'CANCELLED'}

        mw = obj.matrix_world
        normal_mat = mw.to_3x3().inverted_safe().transposed()
        avg_normal = mathutils.Vector((0.0, 0.0, 0.0))
        avg_center = mathutils.Vector((0.0, 0.0, 0.0))
        for f in selected:
            avg_normal += (normal_mat @ f.normal).normalized()
            avg_center += mw @ f.calc_center_median()
        avg_center /= len(selected)

        if avg_normal.length < 1e-6:
            self.report({'WARNING'}, "Selected normals cancel out; cannot align")
            return {'CANCELLED'}
        avg_normal.normalize()

        rv3d = context.space_data.region_3d
        rv3d.view_perspective = 'ORTHO'
        rv3d.view_rotation = (-avg_normal).to_track_quat('Z', 'Y')
        rv3d.view_location = avg_center
        return {'FINISHED'}


class MESH_OT_ShapeKeysToBones(Operator):
    bl_idname = "kitsunetools.shapekeys_to_bones"
    bl_label = "Shape Keys to Bones"
    bl_description = ("Approximate the shape keys with translation-only bones and weights, "
                      "with one pose per shape key stored in a new action")
    bl_options = {'REGISTER', 'UNDO'}

    bone_count: IntProperty(name="Bone Count", default=24, min=1, max=256)
    max_influences: IntProperty(name="Max Influences", default=4, min=1, max=8)
    motion_threshold: FloatProperty(name="Motion Threshold",
        description="Vertices moving less than this fraction of a shape key's largest offset are ignored for that key",
        default=0.05, min=0.001, max=0.5, subtype='FACTOR')
    spatial_weight: FloatProperty(name="Spatial Weight",
        description="How much vertex position, versus motion, decides which bone a vertex belongs to",
        default=1.0, min=0.0, max=10.0)
    smooth_iterations: IntProperty(name="Smooth", default=1, min=0, max=10)
    solver_iterations: IntProperty(name="Iterations", default=3, min=1, max=10)
    include_muted: BoolProperty(name="Include Muted Keys", default=False)
    mute_shape_keys: BoolProperty(name="Mute Converted Keys",
        description="Mute the converted shape keys so they don't stack on top of the bone poses", default=True)
    bone_prefix: StringProperty(name="Prefix", default="FX_")
    parent_bone: StringProperty(name="Parent Bone", description="Bone the face bones are parented to, usually the head")

    @classmethod
    def poll(cls, context: Context) -> bool:
        ob = context.active_object
        return bool(is_mesh(ob) and ob.mode == 'OBJECT' and ob.data.shape_keys and len(ob.data.shape_keys.key_blocks) > 1)

    @staticmethod
    def find_armature(ob: Object) -> Object | None:
        return next((m.object for m in ob.modifiers if m.type == 'ARMATURE' and m.object), None)

    def invoke(self, context, event):
        arm = self.find_armature(context.active_object)
        if arm and self.parent_bone not in arm.data.bones:
            names = [b.name for b in arm.data.bones]
            self.parent_bone = next((n for n in names if n.lower() == 'head'),
                                    next((n for n in names if 'head' in n.lower()), ''))
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False

        arm = self.find_armature(context.active_object)
        if arm:
            col.prop_search(self, 'parent_bone', arm.data, 'bones')
        else:
            col.label(text="No armature modifier, a new armature will be created", icon='INFO')
        col.separator()
        col.prop(self, 'bone_count')
        col.prop(self, 'max_influences')
        col.prop(self, 'motion_threshold', slider=True)
        col.prop(self, 'spatial_weight')
        col.prop(self, 'smooth_iterations')
        col.prop(self, 'solver_iterations')
        col.separator()
        col.prop(self, 'bone_prefix')
        col.prop(self, 'include_muted')
        col.prop(self, 'mute_shape_keys')

    def execute(self, context: Context) -> set:
        ob = context.active_object
        mesh = ob.data
        arm = self.find_armature(ob)

        if arm:
            if self.parent_bone not in arm.data.bones:
                self.report({'ERROR'}, "Pick a parent bone for the face bones")
                return {'CANCELLED'}
            if arm.library or not arm.visible_get():
                self.report({'ERROR'}, f"Armature '{arm.name}' must be local and visible")
                return {'CANCELLED'}
            space = arm.matrix_world.inverted() @ ob.matrix_world
        else:
            space = None

        rest, deltas, key_names = read_shapekey_deltas(ob, self.include_muted, space)
        if not key_names:
            self.report({'WARNING'}, "No shape keys to convert")
            return {'CANCELLED'}

        edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
        mesh.edges.foreach_get('vertices', edges)

        result = solve_shapekey_bones(
            rest, deltas, edges.reshape(-1, 2).astype(np.int64),
            bone_count=self.bone_count, max_influences=self.max_influences,
            motion_threshold=self.motion_threshold, spatial_weight=self.spatial_weight,
            smooth_iterations=self.smooth_iterations, solver_iterations=self.solver_iterations,
        )
        if result is None:
            self.report({'WARNING'}, "Shape keys don't move any vertices")
            return {'CANCELLED'}

        indices, weights, heads = result['indices'], result['weights'], result['heads']

        normals = np.empty(len(mesh.vertices) * 3)
        mesh.vertex_normals.foreach_get('vector', normals)
        normals = normals.reshape(-1, 3)[indices]
        if space is not None:
            normals = normals @ np.array(space.to_3x3()).T
        bone_dirs = weights.T @ normals
        bone_length = 0.04 * np.linalg.norm(np.ptp(rest[indices], axis=0))

        new_root = arm is None
        if new_root:
            arm_data = bpy.data.armatures.new(f"{ob.name}_FaceRig")
            arm = bpy.data.objects.new(arm_data.name, arm_data)
            for coll in ob.users_collection:
                coll.objects.link(arm)
            arm.matrix_world = ob.matrix_world.copy()
            arm.show_in_front = True

            ob.modifiers.new(name=arm.name, type='ARMATURE').object = arm
            if ob.parent is None:
                ob.parent = arm
                ob.matrix_parent_inverse = arm.matrix_world.inverted()

        bone_collection = arm.data.collections.get("Face Shapes") or arm.data.collections.new("Face Shapes")
        bone_names = []
        with preserve_context_mode(arm, 'EDIT') as edit_bones:
            if new_root:
                root = edit_bones.new("FaceRoot")
                root.head = mathutils.Vector(rest.mean(0))
                root.tail = root.head + mathutils.Vector((0.0, 0.0, bone_length * 5.0))
                self.parent_bone = root.name
            parent = edit_bones[self.parent_bone]

            for b, head in enumerate(heads):
                direction = mathutils.Vector(bone_dirs[b])
                if direction.length < 1e-8:
                    direction = mathutils.Vector((0.0, 0.0, 1.0))
                eb = edit_bones.new(f"{self.bone_prefix}{key_names[result['dominant_key'][b]]}")
                eb.head = mathutils.Vector(head)
                eb.tail = eb.head + direction.normalized() * bone_length
                eb.parent = parent
                eb.use_connect = False
                eb.use_deform = True
                bone_collection.assign(eb)
                bone_names.append(eb.name)

        # Pose location is in the bone's rest space, so armature-space offsets are rotated into it
        translations = result['translations']
        locations = np.empty_like(translations)
        for b, name in enumerate(bone_names):
            rot = np.array(arm.data.bones[name].matrix_local.to_3x3())
            locations[:, b] = translations[:, b] @ rot

        deform_groups = {bone.name for bone in arm.data.bones if bone.use_deform}
        assign_shapekey_bone_weights(ob, indices, weights, bone_names, self.parent_bone, deform_groups)
        action = write_shapekey_pose_action(arm, f"{ob.name}_ShapeKeyPoses", bone_names, key_names, locations)

        if self.mute_shape_keys:
            for name in key_names:
                mesh.shape_keys.key_blocks[name].mute = True

        assigned = arm.animation_data.action == action
        self.report({'INFO'}, f"Created {len(bone_names)} bones ({result['fit'] * 100:.0f}% fit), poses in action "
                              f"'{action.name}'" + ("" if assigned else " (not assigned, armature already has an action)"))
        return {'FINISHED'}


class MESH_OT_CreateHairShadow(Operator):
    bl_idname = "kitsunetools.create_hair_shadow"
    bl_label = "Create Hair Shadow Mesh"
    bl_description = ("Flatten the selected faces (e.g. bangs) onto a target mesh (e.g. the face) "
                      "as a baked anime/MMD-style hair shadow mesh")
    bl_options = {'REGISTER', 'UNDO'}

    target: StringProperty(name="Target", description="Mesh the shadow is projected onto, usually the face")
    projection: EnumProperty(
        name="Projection",
        items=[
            ('FRONT', "Front Axis", "Project along the front view axis (+Y), toward a -Y facing character"),
            ('VIEW', "View", "Project along the viewport direction at the time the tool was invoked"),
            ('NEAREST', "Nearest Surface", "Snap each vertex to the closest point on the target"),
        ],
        default='FRONT'
    )
    view_direction: FloatVectorProperty(size=3, default=(0.0, 1.0, 0.0), options={'HIDDEN', 'SKIP_SAVE'})
    drop: FloatProperty(
        name="Drop",
        description="Shift the shadow down (global -Z) before projecting, as if lit from above",
        default=0.0, subtype='DISTANCE', precision=4
    )
    offset: FloatProperty(
        name="Surface Offset",
        description="Distance kept above the target surface to avoid clipping and z-fighting",
        default=0.0005, min=0.0, subtype='DISTANCE', precision=4
    )
    search_back: FloatProperty(
        name="Search Back",
        description="Start each ray this far behind the vertex so hair clipping into the face still projects",
        default=0.02, min=0.0, subtype='DISTANCE', precision=3
    )
    subdivisions: IntProperty(
        name="Subdivisions",
        description="Subdivide before projecting so the shadow follows the face curvature",
        default=1, min=0, max=6
    )
    single_layer: BoolProperty(
        name="Single Layer",
        description="Keep only the hair faces pointing away from the target so double-sided strands do not stack",
        default=True
    )
    rig_to_target: BoolProperty(
        name="Rig to Target",
        description="Copy the target's weights, armature modifiers and parent so the shadow follows the face",
        default=True
    )
    add_material: BoolProperty(name="Add Material", default=True)
    shadow_color: FloatVectorProperty(
        name="Shadow Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.55, 0.3, 0.3, 0.5)
    )

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def invoke(self, context, event):
        hair_ob = context.active_object
        if not self.target or self.target == hair_ob.name:
            other = next((ob for ob in context.selected_objects if ob != hair_ob and ob.type == 'MESH'), None)
            self.target = other.name if other else ""
        rv3d = context.region_data
        if rv3d is not None and context.area and context.area.type == 'VIEW_3D':
            self.view_direction = rv3d.view_rotation @ mathutils.Vector((0.0, 0.0, -1.0))
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "target", context.scene, "objects")
        layout.prop(self, "projection")
        layout.prop(self, "drop")
        layout.prop(self, "offset")
        if self.projection != 'NEAREST':
            layout.prop(self, "search_back")
        layout.prop(self, "subdivisions")
        layout.prop(self, "single_layer")
        layout.prop(self, "rig_to_target")
        layout.prop(self, "add_material")
        if self.add_material:
            layout.prop(self, "shadow_color")

    def execute(self, context):
        hair_ob = context.active_object
        target_ob = bpy.data.objects.get(self.target)
        if target_ob is None or target_ob.type != 'MESH' or target_ob == hair_ob:
            self.report({'ERROR'}, "Pick a target mesh other than the hair")
            return {'CANCELLED'}

        if self.projection == 'FRONT':
            direction = mathutils.Vector((0.0, 1.0, 0.0))
        elif self.projection == 'VIEW':
            direction = mathutils.Vector(self.view_direction).normalized()
        else:
            direction = None

        name = f"{hair_ob.name}_HairShadow"
        mesh = create_hair_shadow_mesh(
            hair_ob, target_ob, name, direction,
            drop=self.drop, offset=self.offset, search_back=self.search_back,
            subdivisions=self.subdivisions, single_layer=self.single_layer,
            copy_weights=self.rig_to_target
        )
        if mesh is None:
            self.report({'WARNING'}, "No faces selected")
            return {'CANCELLED'}

        new_ob = bpy.data.objects.new(name, mesh)
        collections = target_ob.users_collection or (context.scene.collection,)
        collections[0].objects.link(new_ob)

        if self.rig_to_target:
            new_ob.parent = target_ob.parent
            new_ob.parent_type = target_ob.parent_type
            new_ob.parent_bone = target_ob.parent_bone
            new_ob.matrix_parent_inverse = target_ob.matrix_parent_inverse.copy()
            new_ob.matrix_basis = target_ob.matrix_basis.copy()
            for vg in target_ob.vertex_groups:
                new_ob.vertex_groups.new(name=vg.name)
            for mod in target_ob.modifiers:
                if mod.type == 'ARMATURE':
                    arm_mod = new_ob.modifiers.new(name=mod.name, type='ARMATURE')
                    arm_mod.object = mod.object
                    arm_mod.use_deform_preserve_volume = mod.use_deform_preserve_volume
        else:
            new_ob.matrix_world = target_ob.matrix_world.copy()

        if self.add_material:
            mesh.materials.append(get_hair_shadow_material("KitsuneTools_HairShadow", self.shadow_color))

        self.report({'INFO'}, f"Hair shadow created: {new_ob.name}")
        return {'FINISHED'}
