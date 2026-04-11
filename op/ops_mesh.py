import bpy, bmesh, mathutils, collections, math
from bpy.types import Operator, Context, Object
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, FloatProperty
from bpy.props import EnumProperty, BoolProperty, FloatProperty, IntProperty
from ..utils.utils_object import is_mesh, has_shapes
from ..utils.utils_mesh import clean_unused_shapekeys
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


class MESH_OT_SelectShapekeyVets(Operator):
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
    
        
class MESH_OT_unlock_all_vertexgroups(bpy.types.Operator):
    bl_idname = "kitsunetools.unlock_all_vertexgroups"
    bl_label = "Unlock All Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and 
                context.active_object.type == 'MESH')
    
    def execute(self, context) -> set:
        selected_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']

        unlocked_count = 0

        for mesh in selected_objects:
            vgroups = mesh.vertex_groups
            for vgroup in vgroups:

                if vgroup.lock_weight:
                    vgroup.lock_weight = False
                    unlocked_count += 1
                
        self.report({'INFO'}, f"Unlocked {unlocked_count} vertex group(s)")
        return {'FINISHED'}


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

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def execute(self, context) -> set:
        original_obj = context.active_object

        with preserve_context_mode(original_obj, 'EDIT'):
            if self.keep_original:
                bpy.ops.mesh.duplicate()

            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            new_obj = next(
                obj for obj in context.selected_objects if obj != original_obj
            )

            bpy.ops.object.select_all(action='DESELECT')
            new_obj.select_set(True)
            context.view_layer.objects.active = new_obj

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
