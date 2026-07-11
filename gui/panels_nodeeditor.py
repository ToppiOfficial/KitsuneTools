import bpy
from bpy.types import Panel, UIList
from ..utils.utils_panels import get_label_with_material_name
from ..utils.utils_object import is_mesh
from ..op.ops_nodeeditor import (
    NODE_OT_node_bake_add,
    NODE_OT_node_bake_all_materials,
    NODE_OT_node_bake_remove,
    NODE_OT_node_bake_run,
    NODE_OT_node_bake_auto_resolution,
    NODE_OT_node_bake_auto_colorspace,
    NODE_OT_node_bake_copy,
    NODE_OT_node_bake_paste
)


class TOOLS_PT_KitsuneTool_Panel(Panel):
    bl_label = ""
    bl_category = 'KitsuneTools'
    bl_region_type = 'UI'
    bl_space_type = 'NODE_EDITOR'


class NODE_UL_nodes_to_bake(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname): # pyright: ignore
        mat = data.id_data
        node = mat.node_tree.nodes.get(item.node_name) if mat and mat.node_tree else None

        if node:
            if node.label:
                display_name = node.label
            elif hasattr(node, 'node_tree') and node.node_tree:
                display_name = node.node_tree.name
            else:
                display_name = node.name
        else:
            display_name = item.node_name if item.node_name else "Select Node..."

        row = layout.row(align=True)
        row.label(text=display_name, icon='NODE')

        sub = row.row()
        sub.alignment = 'RIGHT'
        if not  item.sync_y_with_x:
            sub.label(text=f"{item.resolution_x}x{item.resolution_y}")
        else:
            sub.label(text=f"{item.resolution_x}")

        sub.label(text=item.color_space)

        if item.name:
            sub.label(text=f"({item.name})")


class NODE_UL_material_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname): # pyright: ignore
        if context.scene.kitsunetools.node_baker_material_listmode == 'ALL':
            mat = item
        else:
            mat = item.material

        if mat:
            row = layout.row(align=True)
            row.prop(mat, "name", text="", icon_value=layout.icon(mat), emboss=False)
            count = len(mat.kitsunetools.node_baker_list)
            if count > 0:
                sub = row.row()
                sub.alignment = 'RIGHT'
                sub.label(text=f"{str(count)} Nodes")
        else:
            layout.label(text="(empty slot)", icon='BLANK1')

    def filter_items(self, context, data, propname): # pyright: ignore
        if context.scene.kitsunetools.node_baker_material_listmode == 'ALL':
            items = list(bpy.data.materials)
            flt_flags = [self.bitflag_filter_item] * len(items)
            flt_neworder = list(range(len(items)))
        else:
            items = list(getattr(data, propname))
            flt_flags = [self.bitflag_filter_item if slot.material else 0 for slot in items]
            flt_neworder = list(range(len(items)))
        return flt_flags, flt_neworder


class NODE_PT_KitsuneTool_NodeBaker(TOOLS_PT_KitsuneTool_Panel):
    def draw_header(self, context):
        curr_ob = context.active_object
        self.layout.label(text=get_label_with_material_name('Node Baker', curr_ob.active_material if curr_ob else None))

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if not is_mesh(obj):
            layout.label(text="Select a Mesh Object", icon='ERROR')
            return

        kt = context.scene.kitsunetools
        listmode = kt.node_baker_material_listmode

        # --- Material selection ---
        header = layout.row(align=True)
        header.label(text="Materials", icon='MATERIAL')
        header.prop(kt, 'node_baker_material_listmode', expand=True)

        if listmode == 'ALL':
            layout.template_list(
                "NODE_UL_material_list", "all_mats",
                bpy.data, "materials",
                kt, "node_baker_material_list_index",
                rows=4
            )
            idx = kt.node_baker_material_list_index
            mat = bpy.data.materials[idx] if 0 <= idx < len(bpy.data.materials) else None
        else:
            layout.template_list(
                "NODE_UL_material_list", "active_slots",
                obj, "material_slots",
                obj, "active_material_index",
                rows=4
            )
            mat = obj.active_material

        if not mat or not mat.use_nodes:
            layout.label(text="Active material has no nodes", icon='INFO')
            return

        mat_name = mat.name if listmode == 'ALL' else ""
        baker_list = mat.kitsunetools.node_baker_list
        list_index = mat.kitsunetools.node_baker_list_index

        # --- Bake items ---
        layout.separator()
        layout.label(text="Bake Items", icon='NODE')
        row = layout.row()
        row.template_list("NODE_UL_nodes_to_bake", "", mat.kitsunetools, "node_baker_list", mat.kitsunetools, "node_baker_list_index", rows=4)

        col = row.column(align=True)
        col.operator(NODE_OT_node_bake_add.bl_idname, icon='ADD', text="").material_name = mat_name
        col.operator(NODE_OT_node_bake_remove.bl_idname, icon='REMOVE', text="").material_name = mat_name
        col.separator()
        col.operator(NODE_OT_node_bake_copy.bl_idname, icon='COPYDOWN', text="").material_name = mat.name
        col.operator(NODE_OT_node_bake_paste.bl_idname, icon='PASTEDOWN', text="").material_name = mat.name

        # --- Active item settings ---
        if len(baker_list) > 0 and 0 <= list_index < len(baker_list):
            item = baker_list[list_index]
            box = layout.box()

            col = box.column()
            col.use_property_split = True
            col.use_property_decorate = False

            col.prop_search(item, "node_name", mat.node_tree, "nodes", text="Node", icon='NODE_SEL')
            col.prop(item, "name", text="Suffix")
            col.prop(item, "socket_index", text="Output")

            col.separator()
            col.prop(item, "has_alpha_channel", text="Alpha Channel")
            if item.has_alpha_channel:
                col.prop(item, "alpha_socket_index", text="Alpha Out")

            col.separator()
            res_row = col.row(align=True)
            res_row.prop(item, "resolution_x", text="Resolution" if item.sync_y_with_x else "Resolution X")
            res_row.prop(item, "sync_y_with_x", text="", icon='LOCKED' if item.sync_y_with_x else 'UNLOCKED')
            if not item.sync_y_with_x:
                col.prop(item, "resolution_y", text="Resolution Y")
            col.prop(item, "color_space", text="Color Space")

            col.separator()
            col.prop(item, "bypass_texture_mapping")

            # Batch helpers for resolution / color space across items
            tools = box.row(align=True)
            tools.operator(NODE_OT_node_bake_auto_resolution.bl_idname, text="Auto Resolution", icon='FIXED_SIZE').material_name = mat.name
            tools.operator(NODE_OT_node_bake_auto_colorspace.bl_idname, text="Set Color Space", icon='IMAGE_RGB').material_name = mat.name

        # --- Output & bake ---
        layout.separator()
        box = layout.box()
        out = box.column()
        out.use_property_split = True
        out.use_property_decorate = False
        out.prop(kt, "node_baker_export_dir")
        out.prop(kt, "node_baker_file_format")

        bake_row = box.row(align=True)
        bake_row.scale_y = 1.4
        bake_row.enabled = len(baker_list) > 0
        op = bake_row.operator(NODE_OT_node_bake_run.bl_idname, text="Bake Selected", icon='RENDER_STILL')
        op.all_items = False
        op.material_name = mat.name
        op = bake_row.operator(NODE_OT_node_bake_run.bl_idname, text="Bake All", icon='RENDERLAYERS')
        op.all_items = True
        op.material_name = mat.name

        if listmode == 'ALL':
            box.operator(NODE_OT_node_bake_all_materials.bl_idname, text="Bake All Materials", icon='MATERIAL')
