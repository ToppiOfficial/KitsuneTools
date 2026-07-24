import bpy, os, re
from bpy.types import Operator
from PIL import Image
from ..utils.utils_object import is_mesh
from bpy.props import EnumProperty, StringProperty, BoolProperty

# Module-level clipboard: list of dicts, one per copied item
_clipboard: list[dict] = []
 
_FIELDS = (
    "node_name",
    "name",
    "resolution_x",
    "resolution_y",
    "sync_y_with_x",
    "color_space",
    "socket_index",
    "has_alpha_channel",
    "alpha_socket_index",
    "bypass_texture_mapping",
)
 
 
def _item_to_dict(item) -> dict:
    return {f: getattr(item, f) for f in _FIELDS}
 
 
def _dict_to_item(d: dict, item) -> None:
    for f, v in d.items():
        setattr(item, f, v)


def _get_target_material(context):
    """Resolve the material the Node Baker panel is currently acting on.

    In 'ALL' list mode this is the material selected in the global material
    list; otherwise it is the active object's active material.
    """
    kt = context.scene.kitsunetools
    if kt.node_baker_material_listmode == 'ALL':
        idx = kt.node_baker_material_list_index
        mats = bpy.data.materials
        return mats[idx] if 0 <= idx < len(mats) else None
    obj = context.active_object
    return obj.active_material if obj else None


def _resolve_material(context, material_name):
    """Prefer an explicit material_name (set by the panel), else fall back to
    the context-derived target so operators also work when run from search."""
    if material_name:
        mat = bpy.data.materials.get(material_name)
        if mat:
            return mat
    return _get_target_material(context)


# ---------------------------------------------------------------------------
# Bake console logging - kept compact and scannable
# ---------------------------------------------------------------------------
_LOG_W = 60


def _log_header(title, subtitle=""):
    print()
    print("=" * _LOG_W)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("=" * _LOG_W)


def _log_footer(summary):
    print("-" * _LOG_W)
    print(f"  {summary}")
    print("=" * _LOG_W)
    print()


def _item_summary(item, node, socket):
    """One-line 'node | socket | res | colorspace [| +alpha]' description."""
    res = str(int(item.resolution_x)) if item.sync_y_with_x else f"{int(item.resolution_x)}x{int(item.resolution_y)}"
    parts = [node.name, socket.name, res, item.color_space]
    if item.has_alpha_channel:
        parts.append("+alpha")
    return "  |  ".join(parts)


class NODE_OT_node_bake_add(Operator):
    bl_idname = "kitsunetools.node_bake_node_add"
    bl_label = "Add Bake Item"
    bl_options = {'UNDO'}

    material_name: bpy.props.StringProperty(default="")
    
    def execute(self, context) -> set:
        mat = bpy.data.materials.get(self.material_name) if self.material_name else context.active_object.active_material
        if not mat:
            return {'CANCELLED'}
        node = context.space_data.node_tree.nodes.active
        item = mat.kitsunetools.node_baker_list.add()
        if node: item.node_name = node.name
        mat.kitsunetools.node_baker_list_index = len(mat.kitsunetools.node_baker_list) - 1
        return {'FINISHED'}


class NODE_OT_node_bake_remove(Operator):
    bl_idname = "kitsunetools.node_bake_node_remove"
    bl_label = "Remove Bake Item"
    bl_options = {'UNDO'}

    material_name: bpy.props.StringProperty(default="")
    
    def execute(self, context) -> set:
        mat = bpy.data.materials.get(self.material_name) if self.material_name else context.active_object.active_material
        if not mat:
            return {'CANCELLED'}
        mat.kitsunetools.node_baker_list.remove(mat.kitsunetools.node_baker_list_index)
        mat.kitsunetools.node_baker_list_index = max(0, mat.kitsunetools.node_baker_list_index - 1)
        return {'FINISHED'}


def _setup_temp_plane(context, mat):
    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.selected_objects]

    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.mesh.primitive_plane_add()
    temp_plane = context.view_layer.objects.active

    # Ensure it's in a selectable collection by moving it to the scene master collection
    for col in temp_plane.users_collection:
        col.objects.unlink(temp_plane)
    context.scene.collection.objects.link(temp_plane)

    temp_plane.data.materials.append(mat)
    return temp_plane, prev_active, prev_selected


def _restore_after_plane(context, temp_plane, prev_active, prev_selected):
    bpy.data.objects.remove(temp_plane, do_unlink=True)
    for o in prev_selected: o.select_set(True)
    context.view_layer.objects.active = prev_active


def _collect_tex_nodes_upstream(start_node):
    """Return all ShaderNodeTexImage nodes reachable upstream from start_node."""
    visited, tex_nodes = set(), []

    def traverse(node):
        if node in visited:
            return
        visited.add(node)
        if node.type == 'TEX_IMAGE':
            tex_nodes.append(node)
        for inp in node.inputs:
            for link in inp.links:
                traverse(link.from_node)

    traverse(start_node)
    return tex_nodes


def _collect_channel_packed_tex_nodes(start_node):
    """Return TEX_IMAGE nodes upstream from start_node whose Alpha output is
    connected. When a texture's alpha is used, its RGB and alpha are independent
    channels packed together, so the image must be treated as Channel Packed
    during bake - otherwise Blender premultiplies the RGB by the alpha and the
    color pass gets corrupted in transparent regions."""
    visited, packed = set(), []

    def traverse(node):
        if node in visited:
            return
        visited.add(node)
        if node.type == 'TEX_IMAGE' and node.image:
            alpha_out = node.outputs.get('Alpha')
            if alpha_out and alpha_out.is_linked:
                packed.append(node)
        for inp in node.inputs:
            for link in inp.links:
                traverse(link.from_node)

    traverse(start_node)
    return packed


def _run_bake_for_material(operator, context, obj, mat, export_path):
    """Bake every item on `mat`. Returns (baked, skipped) counts."""
    items = list(mat.kitsunetools.node_baker_list)
    total = len(items)
    fmt = context.scene.kitsunetools.node_baker_file_format
    ext = ".png" if fmt == 'PNG' else ".tga"

    if total == 0:
        print("    (no items)")
        return 0, 0

    baked = skipped = 0
    for item_idx, item in enumerate(items):
        node = mat.node_tree.nodes.get(item.node_name)
        if not node:
            print(f"    [{item_idx + 1}/{total}] SKIP  node '{item.node_name}' not found")
            skipped += 1
            continue

        socket = node.outputs[int(item.socket_index)]
        suffix = item.name if item.name else socket.name
        filename = f"{mat.name}_{suffix}"

        print(f"    [{item_idx + 1}/{total}] {filename}{ext}")
        print(f"          {_item_summary(item, node, socket)}")

        temp_col = os.path.join(export_path, f"_temp_col_{mat.name}.tga")
        temp_alpha = os.path.join(export_path, f"_temp_alpha_{mat.name}.tga")

        temp_plane, prev_active, prev_selected = _setup_temp_plane(context, mat)
        bake_obj = temp_plane

        try:
            operator._process_bake(context, bake_obj, mat, node, int(item.socket_index), item, temp_col, save_alpha=item.has_alpha_channel)

            if item.has_alpha_channel:
                operator._process_bake(context, bake_obj, mat, node, int(item.alpha_socket_index), item, temp_alpha, force_colorspace='Non-Color')
                operator._merge_with_pil(temp_col, temp_alpha, export_path, filename, fmt)
            else:
                final_path = os.path.normpath(os.path.join(export_path, filename + ext))
                if os.path.exists(final_path): os.remove(final_path)
                os.rename(temp_col, final_path)

        finally:
            for p in [temp_col, temp_alpha]:
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass
            _restore_after_plane(context, temp_plane, prev_active, prev_selected)

        baked += 1

    return baked, skipped

#
#   FIXME: Somewhere in the process can cause a hang that even keyboard interrupt doesn't seem to work !!
#
class NODE_OT_node_bake_run(Operator):
    bl_idname = "kitsunetools.node_bake_run"
    bl_label = "Run Node Bake"
    all_items: bpy.props.BoolProperty(default=False)
    material_name: bpy.props.StringProperty(default="")

    def execute(self, context) -> set:
        mat = _resolve_material(context, self.material_name)
        if not mat or not mat.node_tree:
            self.report({'WARNING'}, "No target material with nodes")
            return {'CANCELLED'}

        kt = mat.kitsunetools
        
        if self.all_items:
            items = list(kt.node_baker_list)
        else:
            if not kt.node_baker_list or kt.node_baker_list_index < 0 or kt.node_baker_list_index >= len(kt.node_baker_list):
                self.report({'WARNING'}, "No item selected in Node Baker list.")
                return {'CANCELLED'}
            items = [kt.node_baker_list[kt.node_baker_list_index]]

        if not items:
            self.report({'WARNING'}, "Node Baker list is empty.")
            return {'CANCELLED'}

        total = len(items)
        fmt = context.scene.kitsunetools.node_baker_file_format
        ext = ".png" if fmt == 'PNG' else ".tga"

        raw_path = bpy.path.abspath(context.scene.kitsunetools.node_baker_export_dir)
        export_path = os.path.normpath(raw_path)
        os.makedirs(export_path, exist_ok=True)

        _log_header(f"Node Baker  -  {mat.name}", f"{total} item(s)  ->  {export_path}")

        baked = skipped = 0
        for item_idx, item in enumerate(items):
            node = mat.node_tree.nodes.get(item.node_name)
            if not node:
                print(f"  [{item_idx + 1}/{total}] SKIP  node '{item.node_name}' not found")
                skipped += 1
                continue

            socket = node.outputs[int(item.socket_index)]
            suffix = item.name if item.name else socket.name
            filename = f"{mat.name}_{suffix}"

            print(f"  [{item_idx + 1}/{total}] {filename}{ext}")
            print(f"        {_item_summary(item, node, socket)}")

            temp_col = os.path.join(export_path, f"_temp_col_{mat.name}.tga")
            temp_alpha = os.path.join(export_path, f"_temp_alpha_{mat.name}.tga")

            temp_plane, prev_active, prev_selected = _setup_temp_plane(context, mat)
            bake_obj = temp_plane

            try:
                self._process_bake(context, bake_obj, mat, node, int(item.socket_index), item, temp_col, save_alpha=item.has_alpha_channel)

                if item.has_alpha_channel:
                    self._process_bake(context, bake_obj, mat, node, int(item.alpha_socket_index), item, temp_alpha, force_colorspace='Non-Color')
                    self._merge_with_pil(temp_col, temp_alpha, export_path, filename, fmt)
                else:
                    final_path = os.path.normpath(os.path.join(export_path, filename + ext))
                    if os.path.exists(final_path): os.remove(final_path)
                    os.rename(temp_col, final_path)

            finally:
                for p in [temp_col, temp_alpha]:
                    if os.path.exists(p):
                        try: os.remove(p)
                        except: pass
                _restore_after_plane(context, temp_plane, prev_active, prev_selected)

            baked += 1

        _log_footer(f"Done  -  {baked} baked, {skipped} skipped")
        self.report({'INFO'}, f"Baked {baked} item(s) from '{mat.name}'")
        return {'FINISHED'}

    def _process_bake(self, context, obj, mat, node, socket_idx, item, filepath, force_colorspace=None, save_alpha=False):
        ntree = mat.node_tree
        res_x = int(item.resolution_x)
        res_y = int(item.resolution_y) if not item.sync_y_with_x else res_x
        colorspace = force_colorspace if force_colorspace else item.color_space

        bake_img = bpy.data.images.new("_temp_bake", width=res_x, height=res_y, alpha=save_alpha)
        bake_img.colorspace_settings.name = colorspace

        mat_out = next((n for n in ntree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
        if not mat_out:
            print(f"        ERROR: no active Material Output node in '{mat.name}'")
            bpy.data.images.remove(bake_img)
            return

        temp_nodes = []
        img_node = ntree.nodes.new('ShaderNodeTexImage')
        img_node.image = bake_img
        temp_nodes.append(img_node)
        ntree.nodes.active = img_node

        emit = ntree.nodes.new('ShaderNodeEmission')
        temp_nodes.append(emit)

        old_links = []
        surf_in = mat_out.inputs['Surface']
        for link in surf_in.links:
            old_links.append((link.from_socket, link.to_socket))
            ntree.links.remove(link)

        ntree.links.new(emit.outputs[0], surf_in)

        socket = node.outputs[socket_idx]
        if socket.type == 'VECTOR':
            print("        note: vector socket - inserting SeparateXYZ + CombineRGB")
            sep = ntree.nodes.new('ShaderNodeSeparateXYZ')
            comb = ntree.nodes.new('ShaderNodeCombineRGB')
            temp_nodes.extend([sep, comb])
            ntree.links.new(socket, sep.inputs[0])
            ntree.links.new(sep.outputs[0], comb.inputs[0])
            ntree.links.new(sep.outputs[1], comb.inputs[1])
            ntree.links.new(sep.outputs[2], comb.inputs[2])
            ntree.links.new(comb.outputs[0], emit.inputs['Color'])
        else:
            ntree.links.new(socket, emit.inputs['Color'])

        scene = context.scene
        old_engine = scene.render.engine
        old_transform = scene.view_settings.view_transform
        old_format = scene.render.image_settings.file_format
        old_cycles_device = scene.cycles.device
        old_samples = scene.cycles.samples

        scene.render.engine = 'CYCLES'
        scene.cycles.bake_type = 'EMIT'
        scene.cycles.samples = 1
        scene.view_settings.view_transform = 'Standard'

        cycles_addon = bpy.context.preferences.addons.get('cycles')
        if cycles_addon:
            cprefs = cycles_addon.preferences
            has_gpu = any(d.use and d.type != 'CPU' for d in cprefs.devices)
            scene.cycles.device = 'GPU' if has_gpu else 'CPU'
        else:
            scene.cycles.device = 'CPU'

        if not obj.data.uv_layers:
            obj.data.uv_layers.new(name="UVMap")

        vector_links = []
        if item.bypass_texture_mapping:
            for tex_node in _collect_tex_nodes_upstream(node):
                vec_input = tex_node.inputs.get('Vector')
                if vec_input and vec_input.links:
                    for link in list(vec_input.links):
                        vector_links.append((link.from_socket, link.to_socket))
                        ntree.links.remove(link)
            if vector_links:
                print(f"        note: bypass mapping - disconnected {len(vector_links)} vector link(s)")

        # Force upstream textures whose Alpha output is connected to Channel Packed
        # so the color pass isn't premultiplied by the alpha. Restored after bake.
        alpha_mode_overrides = {}
        for tex_node in _collect_channel_packed_tex_nodes(node):
            img = tex_node.image
            if img.name not in alpha_mode_overrides and img.alpha_mode != 'CHANNEL_PACKED':
                alpha_mode_overrides[img.name] = (img, img.alpha_mode)
                img.alpha_mode = 'CHANNEL_PACKED'
        if alpha_mode_overrides:
            print(f"        note: alpha connection - forced channel-packed on {len(alpha_mode_overrides)} image(s)")

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.bake(type='EMIT')

        for img, mode in alpha_mode_overrides.values():
            img.alpha_mode = mode

        for f, t in vector_links:
            ntree.links.new(f, t)

        if not save_alpha:
            pixels = list(bake_img.pixels)
            for i in range(3, len(pixels), 4):
                pixels[i] = 1.0
            bake_img.pixels = pixels

        bake_img.filepath_raw = os.path.normpath(filepath)
        bake_img.file_format = 'TARGA'
        bake_img.save()

        for n in temp_nodes: ntree.nodes.remove(n)
        for f, t in old_links: ntree.links.new(f, t)
        bpy.data.images.remove(bake_img)

        scene.render.engine = old_engine
        scene.cycles.device = old_cycles_device
        scene.cycles.samples = old_samples
        scene.view_settings.view_transform = old_transform
        scene.render.image_settings.file_format = old_format

    def _merge_with_pil(self, col_path, alpha_path, export_dir, filename, fmt):
        with Image.open(col_path).convert("RGBA") as base_img:
            with Image.open(alpha_path).convert("L") as alpha_mask:
                ext = ".png" if fmt == 'PNG' else ".tga"
                save_path = os.path.normpath(os.path.join(export_dir, filename + ext))

                # A fully opaque alpha carries no information - drop it and save RGB.
                if alpha_mask.getextrema()[0] == 255:
                    print("        note: alpha is fully opaque - saved as RGB")
                    base_img.convert("RGB").save(save_path)
                    return

                r, g, b, _ = base_img.split()
                Image.merge("RGBA", (r, g, b, alpha_mask)).save(save_path)


class NODE_OT_node_bake_all_materials(Operator):
    bl_idname = "kitsunetools.node_bake_all_materials"
    bl_label = "Bake All Materials"

    def invoke(self, context, event):
        if context.scene.kitsunetools.node_baker_material_listmode == 'ALL':
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context) -> set:
        obj = context.active_object
        if not obj or not is_mesh(obj):
            self.report({'ERROR'}, "No active mesh object.")
            return {'CANCELLED'}

        listmode = context.scene.kitsunetools.node_baker_material_listmode

        if listmode == 'ALL':
            material_slots = [
                type('S', (), {'material': m})()
                for m in bpy.data.materials
                if m.use_nodes and len(m.kitsunetools.node_baker_list) > 0
            ]
        else:
            material_slots = [slot for slot in obj.material_slots if slot.material and slot.material.use_nodes]
            
        total_mats = len(material_slots)

        if total_mats == 0:
            self.report({'WARNING'}, "No materials with node trees found on this object.")
            return {'CANCELLED'}

        raw_path = bpy.path.abspath(context.scene.kitsunetools.node_baker_export_dir)
        export_path = os.path.normpath(raw_path)
        os.makedirs(export_path, exist_ok=True)

        _log_header(f"Node Baker  -  Bake All Materials", f"'{obj.name}'  |  {total_mats} material(s)  ->  {export_path}")

        tot_baked = tot_skipped = 0
        for mat_idx, slot in enumerate(material_slots):
            mat = slot.material
            total_items = len(mat.kitsunetools.node_baker_list)
            print(f"\n  Material [{mat_idx + 1}/{total_mats}]  {mat.name}  ({total_items} item(s))")
            obj.active_material_index = mat_idx
            b, s = _run_bake_for_material(self, context, obj, mat, export_path)
            tot_baked += b
            tot_skipped += s

        _log_footer(f"All done  -  {tot_baked} baked, {tot_skipped} skipped, {total_mats} material(s)")
        self.report({'INFO'}, f"Baked {tot_baked} item(s) across {total_mats} material(s) on '{obj.name}'")
        return {'FINISHED'}

    def _process_bake(self, context, obj, mat, node, socket_idx, item, filepath, force_colorspace=None, save_alpha=False):
        return NODE_OT_node_bake_run._process_bake(self, context, obj, mat, node, socket_idx, item, filepath, force_colorspace, save_alpha)

    def _merge_with_pil(self, col_path, alpha_path, export_dir, filename, fmt):
        return NODE_OT_node_bake_run._merge_with_pil(self, col_path, alpha_path, export_dir, filename, fmt)
    

class NODE_OT_import_custom_nodes(Operator):
    bl_idname = "kitsunetools.import_custom_nodes"
    bl_label = "Import Kitsune Custom Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    overwrite: bpy.props.BoolProperty(default=True)
    _conflicts: set = set()

    @staticmethod
    def _get_blend_path():
        addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(addon_dir, "externalfiles", "shadernodes.blend")

    @staticmethod
    def _get_conflicting_names(blend_path):
        existing = set(ng.name for ng in bpy.data.node_groups)
        conflicts = set()
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, _):
            for name in data_from.node_groups:
                if name in existing:
                    conflicts.add(name)
        return conflicts

    @staticmethod
    def _update_materials(old_name, new_node_group):
        for mat in bpy.data.materials:
            if not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'GROUP' and node.node_tree and node.node_tree.name == old_name:
                    node.node_tree = new_node_group

    def _import_nodes(self, blend_path):
        old_groups = {name: bpy.data.node_groups.get(name) for name in self._conflicts}

        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            data_to.node_groups = data_from.node_groups

        for ng in data_to.node_groups:
            if ng:
                ng.use_fake_user = True

        if self.overwrite:
            for name, old_ng in old_groups.items():
                new_ng = next(
                    (ng for ng in bpy.data.node_groups if ng.name.startswith(name) and ng != old_ng),
                    None
                )
                if old_ng and new_ng:
                    self._update_materials(name, new_ng)
                    new_ng.name = name + "__tmp"
                    bpy.data.node_groups.remove(old_ng)
                    new_ng.name = name

        for lib in bpy.data.libraries:
            if lib.filepath == blend_path:
                bpy.data.libraries.remove(lib)

    def invoke(self, context, event) -> set:
        blend_path = self._get_blend_path()

        if not os.path.exists(blend_path):
            self.report({'ERROR'}, f"Shader nodes file not found: {blend_path}")
            return {'CANCELLED'}

        self._conflicts = self._get_conflicting_names(blend_path)

        if self._conflicts:
            return context.window_manager.invoke_props_dialog(self, width=400)

        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="The following node groups already exist:", icon='ERROR')
        box = layout.box()
        for name in sorted(self._conflicts):
            box.label(text=f"  • {name}")
        layout.separator()
        layout.prop(self, "overwrite", text="Overwrite and update existing nodes")

    def execute(self, context) -> set:
        blend_path = self._get_blend_path()

        if not os.path.exists(blend_path):
            self.report({'ERROR'}, f"Shader nodes file not found: {blend_path}")
            return {'CANCELLED'}

        if self._conflicts and not self.overwrite:
            self.report({'INFO'}, "Import cancelled - existing nodes were not overwritten.")
            return {'CANCELLED'}

        self._import_nodes(blend_path)

        for area in context.screen.areas:
            area.tag_redraw()

        action = "imported and updated" if self.overwrite else "imported"
        self.report({'INFO'}, f"Shader nodes {action} successfully.")
        return {'FINISHED'}
    

class NODE_OT_copy_node_values(Operator):
    bl_idname = "node.copy_node_values"
    bl_label = "Copy Node Values"
    bl_description = "Copy adjustable values from the active shader node to matching nodes in target materials"
    bl_options = {'REGISTER', 'UNDO'}
 
    scope: EnumProperty(
        name="Scope",
        items=[
            ('ACTIVE_MATERIAL', "Active Material Only", "Copy only within the current active material"),
            ('OBJECT_MATERIALS', "All Object Materials", "Copy to all materials on the active object"),
            ('ALL', "All Materials in File", "Copy to every material in the blend file"),
        ],
        default='ALL',
    )
 
    copy_mode: EnumProperty(
        name="Copy Mode",
        items=[
            ('ALL', "Copy All Settings", "Copy all adjustable values"),
            ('SELECTED', "Copy Only Selected Input", "Copy only the chosen input value"),
        ],
        default='ALL',
    )
 
    selected_input: StringProperty(
        name="Input Name",
        description="Name of the specific input to copy when Copy Mode is 'Copy Only Selected'",
        default="",
    )
 
    match_by_name: BoolProperty(
        name="Match by Name",
        description="Only match nodes whose Name matches the source node",
        default=False,
    )
 
    match_by_label: BoolProperty(
        name="Match by Label",
        description="Only match nodes whose Label matches the source node",
        default=False,
    )
 
    def _active_node(self, context):
        space = context.space_data
        if space and space.type == 'NODE_EDITOR' and space.tree_type == 'ShaderNodeTree':
            return space.node_tree.nodes.active if space.node_tree else None
        return None
 
    def _copyable_inputs(self, node):
        return [
            inp for inp in node.inputs
            if not inp.is_linked
            and hasattr(inp, 'default_value')
            and isinstance(inp.default_value, (float, int, bool))
        ]
 
    def _node_matches(self, source, candidate):
        if candidate is source:
            return False
        if candidate.type != source.type:
            return False
        if source.type == 'GROUP' and candidate.node_tree != source.node_tree:
            return False
        if self.match_by_name and candidate.name != source.name:
            return False
        if self.match_by_label and candidate.label != source.label:
            return False
        return True
 
    def _target_materials(self, context):
        if self.scope == 'ACTIVE_MATERIAL':
            mat = context.object.active_material if context.object else None
            return [mat] if mat else []
        if self.scope == 'OBJECT_MATERIALS':
            obj = context.object
            return [slot.material for slot in obj.material_slots if slot.material] if obj else []
        return [mat for mat in bpy.data.materials if mat.use_nodes]
 
    def invoke(self, context, event) -> set:
        source = self._active_node(context)
        if not source:
            self.report({'WARNING'}, "No active shader node selected in the Shader Editor.")
            return {'CANCELLED'}
        if not self._copyable_inputs(source):
            self.report({'WARNING'}, "Active node has no copyable float/int values.")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=340)
 
    def draw(self, context):
        layout = self.layout
        source = self._active_node(context)
 
        layout.label(text=f"Source Node: {source.name if source else 'None'}", icon='NODE')
        layout.separator()
        layout.prop(self, "scope")
        layout.separator()
        layout.prop(self, "copy_mode")
 
        if self.copy_mode == 'SELECTED' and source:
            col = layout.column()
            col.label(text="Select Input to Copy:")
            for inp in self._copyable_inputs(source):
                icon = 'RADIOBUT_ON' if self.selected_input == inp.name else 'RADIOBUT_OFF'
                col.operator(
                    NODE_OT_set_copy_input.bl_idname,
                    text=inp.name,
                    icon=icon,
                    emboss=False,
                ).input_name = inp.name
 
        layout.separator()
        layout.prop(self, "match_by_name")
        layout.prop(self, "match_by_label")
 
    def execute(self, context) -> set:
        source = self._active_node(context)
        if not source:
            self.report({'ERROR'}, "No active shader node.")
            return {'CANCELLED'}
 
        selected_input = self.selected_input if self.copy_mode == 'SELECTED' else None
        count = 0
 
        for mat in self._target_materials(context):
            if not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if not self._node_matches(source, node):
                    continue
                for src_inp in self._copyable_inputs(source):
                    if selected_input and src_inp.name != selected_input:
                        continue
                    tgt_inp = node.inputs.get(src_inp.name)
                    if tgt_inp and not tgt_inp.is_linked and type(tgt_inp) == type(src_inp):
                        tgt_inp.default_value = src_inp.default_value
                count += 1
 
        self.report({'INFO'}, f"Copied values to {count} node(s).")
        return {'FINISHED'}
 

class NODE_OT_set_copy_input(Operator):
    """Sets the selected input on the parent copy operator via window manager storage."""
    bl_idname = "node.set_copy_input_selection"
    bl_label = "Select Input"
    bl_options = {'INTERNAL'}
 
    input_name: StringProperty()
 
    def execute(self, context) -> set:
        context.window_manager['_copy_node_selected_input'] = self.input_name #pyright: ignore
        return {'FINISHED'}

class NODE_OT_node_bake_auto_resolution(Operator):
    bl_idname = "node.node_bake_auto_resolution"
    bl_label = "Auto Resolution"
    bl_description = "Set resolution from the largest of all connected Image Texture nodes"

    material_name: StringProperty(default="")

    mode: bpy.props.EnumProperty(
        items=[
            ('ACTIVE',         "Active Item",              "Only the active item in the active material"),
            ('ALL_ACTIVE_MAT', "All in Active Material",   "All items in the active material"),
            ('ALL_MATERIALS',  "All Materials",            "All items across all materials"),
        ],
        default='ACTIVE',
    )

    filter_regex: StringProperty(
        name="Filter Regex",
        description="Only process items whose match field satisfies this regex. Leave empty to match all.",
        default="",
    )

    filter_by: bpy.props.EnumProperty(
        name="Filter By",
        items=[
            ('ITEM_NAME',  "Item Name",  "Match against the baker item's suffix name"),
            ('NODE_NAME',  "Node Name",  "Match against the node's internal name"),
            ('NODE_LABEL', "Node Label", "Match against the node's label"),
        ],
        default='ITEM_NAME',
    )

    reducer: bpy.props.IntProperty(
        name="Reducer",
        description="Divide the resolution before snapping. 1 = no reduction, 2 = half, etc.",
        default=1,
        min=1,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode")
        layout.prop(self, "reducer")
        layout.separator()
        layout.label(text="Filter:")
        layout.prop(self, "filter_by", text="")
        layout.prop(self, "filter_regex", text="Regex")

    def _get_materials(self, context):
        if self.mode == 'ALL_MATERIALS':
            return [m for m in bpy.data.materials if m.use_nodes and m.kitsunetools.node_baker_list]

        if self.material_name:
            mat = bpy.data.materials.get(self.material_name)
        else:
            obj = context.active_object
            mat = obj.active_material if obj else None
        return [mat] if mat and mat.use_nodes else []

    def _get_items(self, mat):
        kt = mat.kitsunetools
        if self.mode == 'ACTIVE':
            if not kt.node_baker_list or kt.node_baker_list_index >= len(kt.node_baker_list):
                return []
            return [kt.node_baker_list[kt.node_baker_list_index]]
        return list(kt.node_baker_list)

    def _matches_filter(self, item, node):
        if not self.filter_regex:
            return True
        try:
            pattern = re.compile(self.filter_regex)
        except re.error:
            return True

        if self.filter_by == 'ITEM_NAME':
            target = item.name
        elif self.filter_by == 'NODE_NAME':
            target = node.name if node else ""
        else:  # NODE_LABEL
            target = node.label if node else ""

        return bool(pattern.search(target))

    def execute(self, context) -> set:
        resolutions = [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]

        materials = self._get_materials(context)
        if not materials:
            self.report({'WARNING'}, "No valid material(s) found")
            return {'CANCELLED'}

        resolved = 0
        for mat in materials:
            for item in self._get_items(mat):
                node = mat.node_tree.nodes.get(item.node_name)

                if not self._matches_filter(item, node):
                    continue
                if not node:
                    continue

                visited = set()
                sizes = []
                stack = [node]
                while stack:
                    current = stack.pop()
                    if current.name in visited:
                        continue
                    visited.add(current.name)
                    if current.type == 'TEX_IMAGE' and current.image and current.image.size[0] > 0:
                        sizes.append((current.image.size[0], current.image.size[1]))
                    for inp in current.inputs:
                        for link in inp.links:  # pyright: ignore
                            if link.from_node.name not in visited:
                                stack.append(link.from_node)

                if not sizes:
                    continue

                target_x = max(w for w, _ in sizes) / self.reducer
                target_y = max(h for _, h in sizes) / self.reducer
                snapped_x = str(min(resolutions, key=lambda r: abs(r - target_x)))
                snapped_y = str(min(resolutions, key=lambda r: abs(r - target_y)))

                item.resolution_x = snapped_x
                if snapped_x != snapped_y:
                    item.sync_y_with_x = False
                    item.resolution_y = snapped_y
                else:
                    item.sync_y_with_x = True
                resolved += 1

        if resolved == 0:
            self.report({'WARNING'}, "No Image Texture nodes with valid images found")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Auto resolution applied to {resolved} item(s)")
        return {'FINISHED'}
    

class NODE_OT_node_bake_auto_colorspace(Operator):
    bl_idname = "node.node_bake_auto_colorspace"
    bl_label = "Set Color Space"
    bl_description = "Set color space on items, optionally filtered by regex"

    material_name: StringProperty(default="")

    mode: bpy.props.EnumProperty(
        items=[
            ('ACTIVE',         "Active Item",            "Only the active item in the active material"),
            ('ALL_ACTIVE_MAT', "All in Active Material", "All items in the active material"),
            ('ALL_MATERIALS',  "All Materials",          "All items across all materials"),
        ],
        default='ACTIVE',
    )

    color_space: bpy.props.EnumProperty(
        name="Color Space",
        items=[
            ('sRGB',      'sRGB (Color)',    ''),
            ('Non-Color', 'Non-Color (Data)', ''),
        ],
        default='sRGB',
    )

    filter_regex: StringProperty(
        name="Filter Regex",
        description="Only process items whose match field satisfies this regex. Leave empty to match all.",
        default="",
    )

    filter_by: bpy.props.EnumProperty(
        name="Filter By",
        items=[
            ('ITEM_NAME',  "Item Name",  "Match against the baker item's suffix name"),
            ('NODE_NAME',  "Node Name",  "Match against the node's internal name"),
            ('NODE_LABEL', "Node Label", "Match against the node's label"),
        ],
        default='ITEM_NAME',
    )

    def _get_materials(self, context):
        if self.mode == 'ALL_MATERIALS':
            return [m for m in bpy.data.materials if m.use_nodes and m.kitsunetools.node_baker_list]
        if self.material_name:
            mat = bpy.data.materials.get(self.material_name)
        else:
            obj = context.active_object
            mat = obj.active_material if obj else None
        return [mat] if mat and mat.use_nodes else []

    def _get_items(self, mat):
        kt = mat.kitsunetools
        if self.mode == 'ACTIVE':
            if not kt.node_baker_list or kt.node_baker_list_index >= len(kt.node_baker_list):
                return []
            return [kt.node_baker_list[kt.node_baker_list_index]]
        return list(kt.node_baker_list)

    def _matches_filter(self, item, node):
        if not self.filter_regex:
            return True
        try:
            pattern = re.compile(self.filter_regex)
        except re.error:
            return True
        if self.filter_by == 'ITEM_NAME':
            target = item.name
        elif self.filter_by == 'NODE_NAME':
            target = node.name if node else ""
        else:  # NODE_LABEL
            target = node.label if node else ""
        return bool(pattern.search(target))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode")
        layout.prop(self, "color_space")
        layout.separator()
        layout.label(text="Filter:")
        layout.prop(self, "filter_by", text="")
        layout.prop(self, "filter_regex", text="Regex")

    def execute(self, context) -> set:
        materials = self._get_materials(context)
        if not materials:
            self.report({'WARNING'}, "No valid material(s) found")
            return {'CANCELLED'}

        applied = 0
        for mat in materials:
            for item in self._get_items(mat):
                node = mat.node_tree.nodes.get(item.node_name)
                if not self._matches_filter(item, node):
                    continue
                item.color_space = self.color_space
                applied += 1

        if applied == 0:
            self.report({'WARNING'}, "No matching items found")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Color space set to '{self.color_space}' on {applied} item(s)")
        return {'FINISHED'}
    
 
class NODE_OT_node_bake_copy(Operator):
    bl_idname = "node.node_bake_copy"
    bl_label = "Copy Node Bake Item(s)"
    bl_description = "Copy active or all node baker list items to clipboard"
 
    all_items: BoolProperty(default=False, name="All Items")
    material_name: StringProperty(default="")

    @classmethod
    def poll(cls, context) -> bool:
        mat = _get_target_material(context)
        return bool(mat and mat.use_nodes and len(mat.kitsunetools.node_baker_list) > 0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "all_items", toggle=True)

    def execute(self, context) -> set:
        global _clipboard
        mat = _resolve_material(context, self.material_name)
        if not mat:
            self.report({'WARNING'}, "No target material")
            return {'CANCELLED'}
        baker_list = mat.kitsunetools.node_baker_list
 
        if self.all_items:
            _clipboard = [_item_to_dict(item) for item in baker_list]
        else:
            idx = mat.kitsunetools.node_baker_list_index
            if not (0 <= idx < len(baker_list)):
                self.report({'WARNING'}, "No active item to copy")
                return {'CANCELLED'}
            _clipboard = [_item_to_dict(baker_list[idx])]
 
        self.report({'INFO'}, f"Copied {len(_clipboard)} item(s)")
        return {'FINISHED'}
 
 
class NODE_OT_node_bake_paste(Operator):
    bl_idname = "node.node_bake_paste"
    bl_label = "Paste Node Bake Item(s)"
    bl_description = "Paste copied node baker items into the active material's list"

    material_name: StringProperty(default="")

    @classmethod
    def poll(cls, context) -> bool:
        mat = _get_target_material(context)
        return bool(mat and mat.use_nodes and bool(_clipboard))

    def execute(self, context) -> set:
        mat = _resolve_material(context, self.material_name)
        if not mat:
            self.report({'WARNING'}, "No target material")
            return {'CANCELLED'}
        baker_list = mat.kitsunetools.node_baker_list
 
        for d in _clipboard:
            item = baker_list.add()
            _dict_to_item(d, item)
 
        mat.kitsunetools.node_baker_list_index = len(baker_list) - 1
        self.report({'INFO'}, f"Pasted {len(_clipboard)} item(s)")
        return {'FINISHED'}