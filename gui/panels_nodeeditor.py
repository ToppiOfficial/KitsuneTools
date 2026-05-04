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
    NODE_OT_node_bake_auto_colorspace
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

            layout.row(align=True).prop(kt, 'node_baker_material_listmode', expand=True)
            layout.label(text="Object Materials:")
            if listmode == 'ALL':
                layout.template_list(
                    "NODE_UL_material_list", "all_mats",
                    bpy.data, "materials",
                    kt, "node_baker_material_list_index"
                )
                idx = kt.node_baker_material_list_index
                mat = bpy.data.materials[idx] if 0 <= idx < len(bpy.data.materials) else None
            else:
                layout.template_list(
                    "NODE_UL_material_list", "active_slots",
                    obj, "material_slots",
                    obj, "active_material_index"
                )
                mat = obj.active_material

            if listmode == 'ALL':
                layout.operator(NODE_OT_node_bake_all_materials.bl_idname, text="Bake All Materials", icon='MATERIAL')

            if not mat or not mat.use_nodes:
                layout.label(text="Active material has no nodes", icon='INFO')
                return

            layout.separator()
            layout.label(text=f"Nodes: {mat.name}")
            row = layout.row()
            row.template_list("NODE_UL_nodes_to_bake", "", mat.kitsunetools, "node_baker_list", mat.kitsunetools, "node_baker_list_index")
            
            col = row.column(align=True)
            op = col.operator(NODE_OT_node_bake_add.bl_idname, icon='ADD', text="")
            op.material_name = mat.name if listmode == 'ALL' else ""
            op = col.operator(NODE_OT_node_bake_remove.bl_idname, icon='REMOVE', text="")
            op.material_name = mat.name if listmode == 'ALL' else ""

            row = layout.row()
            op = row.operator(NODE_OT_node_bake_auto_resolution.bl_idname)
            op.material_name = mat.name
            op = row.operator(NODE_OT_node_bake_auto_colorspace.bl_idname)
            op.material_name = mat.name

            def _draw_split(box, label, prop_owner, prop_name, **kwargs):
                split = box.split(factor=0.4)
                split.alignment = 'RIGHT'
                split.label(text=label)
                split.prop(prop_owner, prop_name, text="", **kwargs)

            if len(mat.kitsunetools.node_baker_list) > 0 and mat.kitsunetools.node_baker_list_index < len(mat.kitsunetools.node_baker_list):
                item = mat.kitsunetools.node_baker_list[mat.kitsunetools.node_baker_list_index]
                box = layout.box()

                row = box.row(align=True)
                row.prop_search(item, "node_name", mat.node_tree, "nodes", text="", icon='NODE_SEL')

                _draw_split(box, "Suffix", item, "name")
                _draw_split(box, "Output", item, "socket_index")

                split = box.split(factor=0.4)
                split.alignment = 'RIGHT'
                split.label(text="")
                split.prop(item, "has_alpha_channel", text="Alpha Channel")

                if item.has_alpha_channel:
                    _draw_split(box, "Alpha Out", item, "alpha_socket_index")

                col = box.column(align=True)
                row = col.row(align=True)
                split = row.split(factor=0.4)
                split.alignment = 'RIGHT'
                split.label(text="X Resolution" if not item.sync_y_with_x else "Resolution")
                sub = split.row(align=True)
                sub.prop(item, "resolution_x", text="")
                sub.prop(item, "sync_y_with_x", text="", icon='LOCKED' if item.sync_y_with_x else 'UNLOCKED', toggle=True, emboss=False)

                if not item.sync_y_with_x:
                    row = col.row(align=True)
                    split = row.split(factor=0.4)
                    split.alignment = 'RIGHT'
                    split.label(text="Y Resolution")
                    sub = split.row(align=True)
                    sub.prop(item, "resolution_y", text="")
                    sub.label(icon='BLANK1')

                col = box.column(align=True)
                row = col.row(align=True)
                split = row.split(factor=0.4)
                split.alignment = 'RIGHT'
                split.label(text="Color Space")
                sub = split.row(align=True)
                sub.prop(item, "color_space", text="")

                split = box.split(factor=0.4)
                split.alignment = 'RIGHT'
                split.label(text="")
                split.prop(item, "bypass_texture_mapping")

            layout.separator()
            layout.prop(context.scene.kitsunetools, "node_baker_export_dir")
            layout.prop(context.scene.kitsunetools, "node_baker_file_format")

            row = layout.row(align=True)
            row.operator(NODE_OT_node_bake_run.bl_idname, text="Bake Selected").all_items = False
            row.operator(NODE_OT_node_bake_run.bl_idname, text="Bake All").all_items = True

            if listmode == 'ALL':
                layout.operator(NODE_OT_node_bake_all_materials.bl_idname, text="Bake All Materials", icon='MATERIAL')
