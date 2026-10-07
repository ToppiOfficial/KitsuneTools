import bpy, os, re, subprocess
import numpy as np
from time import perf_counter
from contextlib import contextmanager
from bpy.types import Operator, PropertyGroup
from ..utils.utils_object import is_mesh
from bpy.props import EnumProperty, StringProperty, BoolProperty, CollectionProperty

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
    "bake_on_mesh",
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


def _log_footer(summary, details=()):
    print("-" * _LOG_W)
    print(f"  {summary}")
    for line in details:
        print(f"  {line}")
    print("=" * _LOG_W)
    print()


_PHASES = ("setup", "color", "alpha", "save")


def _fmt_times(times):
    """'setup 0.12s  color 1.23s  ...  =  2.75s', skipping phases that did not run."""
    parts = [f"{p} {times[p]:.2f}s" for p in _PHASES if times.get(p)]
    return "  ".join(parts) + f"  =  {sum(times.get(p, 0.0) for p in _PHASES):.2f}s"


def _timing_details(totals, elapsed, device):
    return [
        f"Device: {device}",
        f"Phases: {_fmt_times(totals)}",
        f"Wall time: {elapsed:.2f}s",
    ]


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


def _setup_temp_plane(bscene, mat):
    me = bpy.data.meshes.new("_kt_bake_plane")
    me.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    me.uv_layers.new(name="UVMap").data.foreach_set("uv", (0, 0, 1, 0, 1, 1, 0, 1))
    me.update()
    me.materials.append(mat)
    obj = bpy.data.objects.new("_kt_bake_plane", me)
    bscene.collection.objects.link(obj)
    return obj


def _find_mesh_with_material(context, mat):
    candidates = [context.active_object] + list(context.scene.objects)
    return next((o for o in candidates if o and o.type == 'MESH' and mat.name in o.data.materials), None)


def _setup_temp_mesh_copy(bscene, mat, src):
    """Copy of src holding only the faces using mat, so procedural coordinates
    (Generated/Object) bake onto its UVs. Texture space is pinned to the source."""
    import bmesh

    obj = src.copy()
    obj.data = src.data.copy()
    bscene.collection.objects.link(obj)
    me = obj.data
    me.use_auto_texspace = False
    me.texspace_location = src.data.texspace_location
    me.texspace_size = src.data.texspace_size

    slot_idx = [i for i, m in enumerate(src.data.materials) if m == mat]
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index not in slot_idx], context='FACES')
    for f in bm.faces:
        f.material_index = 0
    bm.to_mesh(me)
    bm.free()
    me.materials.clear()
    me.materials.append(mat)
    obj.material_slots[0].link = 'DATA'
    return obj


def _setup_bake_object(context, bscene, mat, item):
    if item.bake_on_mesh:
        src = _find_mesh_with_material(context, mat)
        if src:
            return _setup_temp_mesh_copy(bscene, mat, src)
        print(f"        note: no mesh uses '{mat.name}' - falling back to plane")
    return _setup_temp_plane(bscene, mat)


def _remove_temp_object(obj):
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)


@contextmanager
def _bake_session(context):
    """Yield (bake scene, device label). Bakes run in a temporary scene holding only the
    bake objects, so each bake's render depsgraph and Cycles sync skip the user's scene."""
    src = context.scene
    cycles_addon = context.preferences.addons.get('cycles')
    cprefs = cycles_addon.preferences if cycles_addon else None
    has_gpu = src.kitsunetools.node_baker_device == 'GPU' and bool(cprefs) and cprefs.compute_device_type != 'NONE' and any(
        d.use and d.type == cprefs.compute_device_type for d in cprefs.devices)

    bscene = bpy.data.scenes.new("_kt_bake_scene")
    try:
        bscene.render.engine = 'CYCLES'
        bscene.view_settings.view_transform = 'Standard'
        bscene.cycles.device = 'GPU' if has_gpu else 'CPU'
        bscene.cycles.samples = 1
        bscene.cycles.bake_type = 'EMIT'
        bscene.render.bake.margin = src.render.bake.margin
        bscene.render.bake.margin_type = src.render.bake.margin_type
        yield bscene, f"GPU ({cprefs.compute_device_type})" if has_gpu else "CPU"
    finally:
        bpy.data.scenes.remove(bscene)


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


def _export_basename(mat_name, filters_str):
    """Strip each comma-separated regex in filters_str from mat_name, then tidy
    leftover separators. Only touches the material name, never the per-export
    suffix. Longest patterns run first so '_ubertrans' wins over 'uber'.
    Invalid patterns are skipped; empty result falls back to mat_name."""
    name = mat_name
    patterns = [p.strip() for p in filters_str.split(",") if p.strip()]
    for pat in sorted(patterns, key=len, reverse=True):
        try:
            name = re.sub(pat, "", name)
        except re.error:
            pass
    name = re.sub(r"[_\-.]{2,}", "_", name).strip("_-. ")
    return name or mat_name


# Luminance weights for reducing a color alpha output to one value
_LUMA = (0.299, 0.587, 0.114)


def _read_pixels(img):
    buf = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf


def _save_image(img, path, fmt):
    img.filepath_raw = path
    img.file_format = 'PNG' if fmt == 'PNG' else 'TARGA'
    img.save()


def _bake_pass(context, bscene, obj, mat, sources, item, colorspace, use_alpha=False, pack=False):
    """Emit-bake (node, socket_idx) sources onto obj's UVs and return the image, or None
    if the material has no active output. With pack, up to 3 sources fill R, G, B in order,
    color sources reduced to luminance. Caller removes the image."""
    ntree = mat.node_tree
    mat_out = next((n for n in ntree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    if not mat_out:
        print(f"        ERROR: no active Material Output node in '{mat.name}'")
        return None

    res_x = int(item.resolution_x)
    res_y = res_x if item.sync_y_with_x else int(item.resolution_y)
    bake_img = bpy.data.images.new("_temp_bake", width=res_x, height=res_y, alpha=use_alpha)
    bake_img.colorspace_settings.name = colorspace

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

    nodes = list({n.as_pointer(): n for n, _ in sources}.values())
    socket = sources[0][0].outputs[sources[0][1]]
    if pack:
        comb = ntree.nodes.new('ShaderNodeCombineXYZ')
        temp_nodes.append(comb)
        for ch, (n, idx) in enumerate(sources):
            out = n.outputs[idx]
            if out.type == 'RGBA':
                dot = ntree.nodes.new('ShaderNodeVectorMath')
                dot.operation = 'DOT_PRODUCT'
                dot.inputs[1].default_value = _LUMA
                temp_nodes.append(dot)
                ntree.links.new(out, dot.inputs[0])
                out = dot.outputs['Value']
            ntree.links.new(out, comb.inputs[ch])
        ntree.links.new(comb.outputs[0], emit.inputs['Color'])
    elif socket.type == 'VECTOR':
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

    if not obj.data.uv_layers:
        obj.data.uv_layers.new(name="UVMap")

    vector_links = []
    if item.bypass_texture_mapping:
        tex_nodes = {t.as_pointer(): t for n in nodes for t in _collect_tex_nodes_upstream(n)}
        for tex_node in tex_nodes.values():
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
    for tex_node in (t for n in nodes for t in _collect_channel_packed_tex_nodes(n)):
        img = tex_node.image
        if img.name not in alpha_mode_overrides and img.alpha_mode != 'CHANNEL_PACKED':
            alpha_mode_overrides[img.name] = (img, img.alpha_mode)
            img.alpha_mode = 'CHANNEL_PACKED'
    if alpha_mode_overrides:
        print(f"        note: alpha connection - forced channel-packed on {len(alpha_mode_overrides)} image(s)")

    vl = bscene.view_layers[0]
    for o in bscene.objects:
        o.select_set(o == obj, view_layer=vl)
    vl.objects.active = obj

    try:
        with context.temp_override(scene=bscene, view_layer=vl, active_object=obj, object=obj,
                                   selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.bake(type='EMIT')
    except Exception:
        bpy.data.images.remove(bake_img)
        raise
    finally:
        for img, mode in alpha_mode_overrides.values():
            img.alpha_mode = mode
        for f, t in vector_links:
            ntree.links.new(f, t)
        for n in temp_nodes:
            ntree.nodes.remove(n)
        for f, t in old_links:
            ntree.links.new(f, t)

    return bake_img


def _bake_item(context, bscene, obj, mat, node, item, final_path, fmt, times, alpha=None):
    """Bake the color pass and the alpha pass (unless alpha is given from a packed bake),
    merge them in memory and save once to final_path. Phase durations go into times.
    Returns False if nothing could be baked."""
    t = perf_counter()
    col_img = _bake_pass(context, bscene, obj, mat, [(node, int(item.socket_index))], item, item.color_space, use_alpha=item.has_alpha_channel)
    times["color"] = perf_counter() - t
    if not col_img:
        return False

    alpha_img = out_img = None
    try:
        if item.has_alpha_channel and alpha is None:
            t = perf_counter()
            alpha_img = _bake_pass(context, bscene, obj, mat, [(node, int(item.alpha_socket_index))], item, 'Non-Color')
            a = _read_pixels(alpha_img).reshape(-1, 4)
            alpha = a[:, :3] @ np.asarray(_LUMA, dtype=np.float32)
            times["alpha"] = perf_counter() - t

        t = perf_counter()
        col = _read_pixels(col_img)
        out_img = col_img
        col[3::4] = 1.0

        if alpha is not None:
            # A fully opaque alpha carries no information - drop it and save RGB.
            if alpha.min() >= 254.5 / 255:
                print("        note: alpha is fully opaque - saved as RGB")
                out_img = bpy.data.images.new("_temp_bake_rgb", width=col_img.size[0], height=col_img.size[1], alpha=False)
                out_img.colorspace_settings.name = col_img.colorspace_settings.name
            else:
                col[3::4] = alpha

        out_img.pixels.foreach_set(col)
        _save_image(out_img, final_path, fmt)
        times["save"] = perf_counter() - t
    finally:
        for img in {col_img, alpha_img, out_img} - {None}:
            bpy.data.images.remove(img)
    return True


def _plan_packed_alpha(mat, items):
    """Map item index -> (chunk, channel). Float/color alpha outputs sharing bake object,
    resolution and mapping bypass are baked 3 per pass, one per RGB channel."""
    groups = {}
    for idx, item in enumerate(items):
        node = mat.node_tree.nodes.get(item.node_name)
        if not item.has_alpha_channel or not node:
            continue
        if node.outputs[int(item.alpha_socket_index)].type not in {'VALUE', 'RGBA'}:
            continue
        res_x = int(item.resolution_x)
        res_y = res_x if item.sync_y_with_x else int(item.resolution_y)
        key = (bool(item.bake_on_mesh), res_x, res_y, bool(item.bypass_texture_mapping))
        groups.setdefault(key, []).append(idx)

    plan = {}
    for idxs in groups.values():
        for start in range(0, len(idxs), 3):
            chunk = tuple(idxs[start:start + 3])
            for ch, idx in enumerate(chunk):
                plan[idx] = (chunk, ch)
    return plan


def _bake_packed_alpha(context, bscene, obj, mat, chunk_items):
    """Bake the alpha outputs of up to 3 items in one pass. Returns (N, 4) pixel rows or None."""
    sources = [(mat.node_tree.nodes[i.node_name], int(i.alpha_socket_index)) for i in chunk_items]
    img = _bake_pass(context, bscene, obj, mat, sources, chunk_items[0], 'Non-Color', pack=True)
    if not img:
        return None
    try:
        return _read_pixels(img).reshape(-1, 4)
    finally:
        bpy.data.images.remove(img)


def _bake_items(context, bscene, mat, items, export_path, pad, totals):
    """Bake items of mat into export_path. Returns (baked, skipped) counts and
    adds phase durations to totals. Bake objects are built once per material."""
    total = len(items)
    if total == 0:
        print(f"{pad}(no items)")
        return 0, 0

    fmt = context.scene.kitsunetools.node_baker_file_format
    ext = ".png" if fmt == 'PNG' else ".tga"
    base = _export_basename(mat.name, context.scene.kitsunetools.node_baker_name_filters)

    baked = skipped = 0
    bake_objs = {}
    plan = _plan_packed_alpha(mat, items)
    packed = {}
    try:
        for item_idx, item in enumerate(items):
            node = mat.node_tree.nodes.get(item.node_name)
            if not node:
                print(f"{pad}[{item_idx + 1}/{total}] SKIP  node '{item.node_name}' not found")
                skipped += 1
                continue

            socket = node.outputs[int(item.socket_index)]
            suffix = item.name if item.name else socket.name
            filename = f"{base}_{suffix}"

            print(f"{pad}[{item_idx + 1}/{total}] {filename}{ext}")
            print(f"{pad}      {_item_summary(item, node, socket)}")

            times = {}
            key = bool(item.bake_on_mesh)
            if key not in bake_objs:
                t = perf_counter()
                bake_objs[key] = _setup_bake_object(context, bscene, mat, item)
                times["setup"] = perf_counter() - t

            alpha = None
            if item_idx in plan:
                chunk, ch = plan[item_idx]
                if chunk not in packed:
                    if len(chunk) > 1:
                        print(f"{pad}      note: alpha packed - {len(chunk)} items share one alpha bake")
                    t = perf_counter()
                    packed[chunk] = _bake_packed_alpha(context, bscene, bake_objs[key], mat, [items[i] for i in chunk])
                    times["alpha"] = perf_counter() - t
                rows = packed[chunk]
                alpha = rows[:, ch] if rows is not None else None
                if item_idx == chunk[-1]:
                    del packed[chunk]

            final_path = os.path.normpath(os.path.join(export_path, filename + ext))
            if _bake_item(context, bscene, bake_objs[key], mat, node, item, final_path, fmt, times, alpha):
                baked += 1
            else:
                skipped += 1

            print(f"{pad}      time: {_fmt_times(times)}")
            for p, v in times.items():
                totals[p] = totals.get(p, 0.0) + v
    finally:
        for obj in bake_objs.values():
            _remove_temp_object(obj)

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

        raw_path = bpy.path.abspath(context.scene.kitsunetools.node_baker_export_dir)
        export_path = os.path.normpath(raw_path)
        os.makedirs(export_path, exist_ok=True)

        _log_header(f"Node Baker  -  {mat.name}", f"{len(items)} item(s)  ->  {export_path}")

        totals = {}
        start = perf_counter()
        with _bake_session(context) as (bscene, device):
            baked, skipped = _bake_items(context, bscene, mat, items, export_path, "  ", totals)

        _log_footer(f"Done  -  {baked} baked, {skipped} skipped", _timing_details(totals, perf_counter() - start, device))
        self.report({'INFO'}, f"Baked {baked} item(s) from '{mat.name}'")
        return {'FINISHED'}


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
        totals = {}
        start = perf_counter()
        with _bake_session(context) as (bscene, device):
            for mat_idx, slot in enumerate(material_slots):
                mat = slot.material
                items = list(mat.kitsunetools.node_baker_list)
                print(f"\n  Material [{mat_idx + 1}/{total_mats}]  {mat.name}  ({len(items)} item(s))")
                obj.active_material_index = mat_idx
                b, s = _bake_items(context, bscene, mat, items, export_path, "    ", totals)
                tot_baked += b
                tot_skipped += s

        _log_footer(f"All done  -  {tot_baked} baked, {tot_skipped} skipped, {total_mats} material(s)",
                    _timing_details(totals, perf_counter() - start, device))
        self.report({'INFO'}, f"Baked {tot_baked} item(s) across {total_mats} material(s) on '{obj.name}'")
        return {'FINISHED'}


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
        before = set(bpy.data.node_groups)

        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            if self.overwrite:
                data_to.node_groups = data_from.node_groups
            else:
                data_to.node_groups = [n for n in data_from.node_groups if n not in self._conflicts]

        for ng in data_to.node_groups:
            if ng:
                ng.use_fake_user = True

        if not self.overwrite:
            # Nested dependencies get appended as "Name.001" copies; point them back to the existing groups
            for ng in [ng for ng in bpy.data.node_groups if ng not in before]:
                base, _, suffix = ng.name.rpartition(".")
                existing = old_groups.get(base) if suffix.isdigit() else None
                if existing:
                    ng.user_remap(existing)
                    bpy.data.node_groups.remove(ng)
        else:
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

        self._conflicts = self._get_conflicting_names(blend_path)

        if not self.overwrite:
            with bpy.data.libraries.load(blend_path, link=False) as (data_from, _):
                missing = [n for n in data_from.node_groups if n not in self._conflicts]
            if not missing:
                self.report({'INFO'}, "All shader nodes already exist - nothing imported.")
                return {'CANCELLED'}

        self._import_nodes(blend_path)

        for area in context.screen.areas:
            area.tag_redraw()

        action = "imported and updated" if self.overwrite else "imported (existing nodes kept)"
        self.report({'INFO'}, f"Shader nodes {action} successfully.")
        return {'FINISHED'}


class NODE_OT_open_custom_nodes_file(Operator):
    bl_idname = "kitsunetools.open_custom_nodes_file"
    bl_label = "Open Kitsune Shader Nodes File"
    bl_description = "Open the bundled shader nodes .blend in a new Blender instance"

    def execute(self, context) -> set:
        blend_path = NODE_OT_import_custom_nodes._get_blend_path()

        if not os.path.exists(blend_path):
            self.report({'ERROR'}, f"Shader nodes file not found: {blend_path}")
            return {'CANCELLED'}

        try:
            subprocess.Popen([bpy.app.binary_path, blend_path])
        except OSError as e:
            self.report({'ERROR'}, f"Failed to launch Blender: {e}")
            return {'CANCELLED'}

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


# Dynamic enum strings must stay referenced or Blender shows garbage labels
_replace_items_cache: dict = {}

_SOCKET_TYPE_MAP = {
    'NodeSocketFloat': 'VALUE',
    'NodeSocketInt': 'INT',
    'NodeSocketBool': 'BOOLEAN',
    'NodeSocketVector': 'VECTOR',
    'NodeSocketColor': 'RGBA',
    'NodeSocketShader': 'SHADER',
}


def _shader_group(name):
    group = bpy.data.node_groups.get(name) if name else None
    return group if group and group.bl_idname == 'ShaderNodeTree' else None


def _group_sockets(group, is_output):
    if not group:
        return []
    in_out = 'OUTPUT' if is_output else 'INPUT'
    return [
        item for item in group.interface.items_tree
        if item.item_type == 'SOCKET' and item.in_out == in_out
    ]


def _replace_target_items(self, context):
    items = [('NONE', "None (disconnect)", "Drop links on this socket")]
    for item in _group_sockets(_shader_group(self.group_name), self.is_output):
        short = _SOCKET_TYPE_MAP.get(item.socket_type, item.socket_type)
        items.append((item.identifier, f"{item.name} [{short}]", ""))
    _replace_items_cache[(self.group_name, self.is_output)] = items
    return items


def _socket_by_identifier(sockets, identifier):
    return next((s for s in sockets if s.identifier == identifier), None)


def _transfer_default(src, dst):
    if not hasattr(src, 'default_value') or not hasattr(dst, 'default_value'):
        return
    value = src.default_value
    try:
        dst.default_value = value
        return
    except (TypeError, ValueError, AttributeError):
        pass
    # Mismatched shapes: copy overlapping channels, or spread a scalar across RGB/XYZ
    src_is_seq = hasattr(value, '__len__')
    if not hasattr(dst.default_value, '__len__'):
        return
    target = list(dst.default_value)
    try:
        if src_is_seq:
            for i in range(min(len(target), len(value))):
                target[i] = value[i]
        else:
            for i in range(min(3, len(target))):
                target[i] = value
        dst.default_value = target
    except (TypeError, ValueError, AttributeError):
        pass


class NodeReplaceSocketMap(PropertyGroup):
    source_id: StringProperty()
    source_name: StringProperty()
    source_type: StringProperty()
    group_name: StringProperty()
    is_output: BoolProperty()
    target: EnumProperty(name="Target", items=_replace_target_items)


class NODE_OT_replace_with_group(Operator):
    bl_idname = "node.replace_with_group"
    bl_label = "Replace Node with Group"
    bl_description = "Replace the active shader node with a node group, remapping links, unlinked values and Node Baker items"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(
        name="Scope",
        items=[
            ('ACTIVE_MATERIAL', "Active Material Only", "Replace only in the material being edited"),
            ('ALL', "All Materials in File", "Replace every matching node in every material"),
        ],
        default='ACTIVE_MATERIAL',
    )
    target_group: StringProperty(name="Replace With", description="Shader node group to replace the node with")
    synced_group: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    input_map: CollectionProperty(type=NodeReplaceSocketMap, options={'SKIP_SAVE'})
    output_map: CollectionProperty(type=NodeReplaceSocketMap, options={'SKIP_SAVE'})

    def _active_node(self, context):
        space = context.space_data
        if space and space.type == 'NODE_EDITOR' and space.tree_type == 'ShaderNodeTree':
            return space.node_tree.nodes.active if space.node_tree else None
        return None

    def _sync_rows(self, source):
        if self.synced_group == self.target_group:
            return
        self.synced_group = self.target_group
        self.input_map.clear()
        self.output_map.clear()
        group = _shader_group(self.target_group)
        if not group:
            return

        for coll, sockets, is_output in (
            (self.input_map, source.inputs, False),
            (self.output_map, source.outputs, True),
        ):
            candidates = _group_sockets(group, is_output)
            used = set()
            for sock in sockets:
                if not getattr(sock, 'enabled', True):
                    continue
                row = coll.add()
                row.source_id = sock.identifier
                row.source_name = sock.name
                row.source_type = sock.type
                row.group_name = group.name
                row.is_output = is_output
                match = next(
                    (c for c in candidates
                     if c.identifier not in used and c.name.casefold() == sock.name.casefold()),
                    None,
                )
                if match:
                    used.add(match.identifier)
                    row.target = match.identifier

    def _node_matches(self, source, candidate):
        if candidate.bl_idname != source.bl_idname:
            return False
        if source.type == 'GROUP' and candidate.node_tree != source.node_tree:
            return False
        return True

    def _target_trees(self, context):
        if self.scope == 'ACTIVE_MATERIAL':
            space = context.space_data
            return [(space.id, space.node_tree)]
        return [(mat, mat.node_tree) for mat in bpy.data.materials if mat.node_tree]

    def _shader_mismatch(self, row):
        target_item = _socket_by_identifier(
            _group_sockets(_shader_group(row.group_name), row.is_output), row.target)
        target_type = _SOCKET_TYPE_MAP.get(target_item.socket_type) if target_item else None
        return (row.source_type == 'SHADER') != (target_type == 'SHADER')

    def invoke(self, context, event) -> set:
        source = self._active_node(context)
        if not source:
            self.report({'WARNING'}, "No active shader node selected in the Shader Editor.")
            return {'CANCELLED'}
        self.synced_group = ""
        self._sync_rows(source)
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        layout = self.layout
        source = self._active_node(context)
        if not source:
            return

        layout.label(text=f"Source Node: {source.name}", icon='NODE')
        layout.prop(self, "scope")
        layout.prop_search(self, "target_group", bpy.data, "node_groups", icon='NODETREE')
        self._sync_rows(source)

        if not _shader_group(self.target_group):
            if self.target_group:
                layout.label(text="Not a shader node group", icon='ERROR')
            return

        for title, rows in (("Inputs", self.input_map), ("Outputs", self.output_map)):
            if not rows:
                continue
            box = layout.box()
            box.label(text=title)
            for row in rows:
                split = box.split(factor=0.4)
                split.label(text=row.source_name)
                sub = split.row(align=True)
                sub.prop(row, "target", text="")
                if row.target != 'NONE' and self._shader_mismatch(row):
                    sub.label(text="", icon='ERROR')

        layout.label(text="Sockets set to None are disconnected", icon='INFO')

    def _replace_node(self, tree, old, group, in_map, out_map):
        new = tree.nodes.new('ShaderNodeGroup')
        new.node_tree = group
        new.parent = old.parent
        new.location = old.location
        new.width = old.width
        new.label = old.label
        new.hide = old.hide
        new.use_custom_color = old.use_custom_color
        new.color = old.color

        for old_sock in old.inputs:
            new_sock = _socket_by_identifier(new.inputs, in_map.get(old_sock.identifier, ''))
            if not new_sock:
                continue
            if old_sock.is_linked:
                for link in old_sock.links:
                    tree.links.new(link.from_socket, new_sock)
            else:
                _transfer_default(old_sock, new_sock)

        for old_sock in old.outputs:
            new_sock = _socket_by_identifier(new.outputs, out_map.get(old_sock.identifier, ''))
            if not new_sock:
                continue
            for link in list(old_sock.links):
                tree.links.new(new_sock, link.to_socket)

        name = old.name
        tree.nodes.remove(old)
        new.name = name
        return new

    def _snapshot_bake_items(self, mat, node):
        # Stores socket identifiers since the stored enum index is meaningless once the node changes
        if not isinstance(mat, bpy.types.Material):
            return []
        outputs = list(node.outputs)

        def identifier_at(index):
            return outputs[int(index)].identifier if index.isdigit() and int(index) < len(outputs) else None

        return [
            (item, identifier_at(item.socket_index),
             identifier_at(item.alpha_socket_index) if item.has_alpha_channel else None)
            for item in mat.kitsunetools.node_baker_list
            if item.node_name == node.name
        ]

    def _remap_bake_items(self, snapshot, new, out_map):
        outputs = list(new.outputs)
        unresolved = 0
        for item, main_id, alpha_id in snapshot:
            pairs = [("socket_index", main_id)]
            if item.has_alpha_channel:
                pairs.append(("alpha_socket_index", alpha_id))
            for attr, old_id in pairs:
                new_sock = _socket_by_identifier(outputs, out_map.get(old_id or '', ''))
                if new_sock:
                    setattr(item, attr, str(outputs.index(new_sock)))
                else:
                    unresolved += 1
        return unresolved

    def execute(self, context) -> set:
        source = self._active_node(context)
        if not source:
            self.report({'ERROR'}, "No active shader node.")
            return {'CANCELLED'}
        self._sync_rows(source)
        group = _shader_group(self.target_group)
        if not group:
            self.report({'ERROR'}, "Pick a shader node group to replace with.")
            return {'CANCELLED'}
        if source.type == 'GROUP' and source.node_tree == group:
            self.report({'WARNING'}, "Node already uses that group.")
            return {'CANCELLED'}

        in_map = {r.source_id: r.target for r in self.input_map if r.target != 'NONE'}
        out_map = {r.source_id: r.target for r in self.output_map if r.target != 'NONE'}
        source_tree = context.space_data.node_tree
        source_name = source.name

        replaced = unresolved = 0
        for mat, tree in self._target_trees(context):
            for node in [n for n in tree.nodes if self._node_matches(source, n)]:
                snapshot = self._snapshot_bake_items(mat, node)
                new = self._replace_node(tree, node, group, in_map, out_map)
                unresolved += self._remap_bake_items(snapshot, new, out_map)
                replaced += 1
                if tree == source_tree and new.name == source_name:
                    tree.nodes.active = new
                    new.select = True

        msg = f"Replaced {replaced} node(s) with '{group.name}'."
        if unresolved:
            self.report({'WARNING'}, f"{msg} {unresolved} bake output(s) had no mapping - check Node Baker items.")
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class NODE_OT_node_bake_auto_resolution(Operator):
    bl_idname = "node.node_bake_auto_resolution"
    bl_label = "Auto Resolution"
    bl_description = "Set resolution from the largest of all connected Image Texture nodes, or 32x32 if the output is a solid color"

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
                has_texture = False
                stack = [node]
                while stack:
                    current = stack.pop()
                    key = current.as_pointer()
                    if key in visited:
                        continue
                    visited.add(key)
                    if current.type.startswith('TEX_'):
                        has_texture = True
                    if current.type == 'TEX_IMAGE' and current.image and current.image.size[0] > 0:
                        sizes.append((current.image.size[0], current.image.size[1]))
                    if current.type == 'GROUP' and current.node_tree:
                        stack.extend(current.node_tree.nodes)
                    for inp in current.inputs:
                        for link in inp.links:  # pyright: ignore
                            stack.append(link.from_node)

                if sizes:
                    target_x = max(w for w, _ in sizes) / self.reducer
                    target_y = max(h for _, h in sizes) / self.reducer
                    snapped_x = str(min(resolutions, key=lambda r: abs(r - target_x)))
                    snapped_y = str(min(resolutions, key=lambda r: abs(r - target_y)))
                elif not has_texture:
                    # No texture nodes upstream or inside groups, output is a solid color
                    snapped_x = snapped_y = "32"
                else:
                    continue

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


class NODE_OT_node_bake_rename_suffix(Operator):
    bl_idname = "node.node_bake_rename_suffix"
    bl_label = "Rename Suffix"
    bl_description = "Regex find/replace on baker item suffixes, e.g. rmao -> rm"
    bl_options = {'UNDO'}

    material_name: StringProperty(default="")

    mode: bpy.props.EnumProperty(
        items=[
            ('ACTIVE',         "Active Item",            "Only the active item in the active material"),
            ('ALL_ACTIVE_MAT', "All in Active Material", "All items in the active material"),
            ('ALL_MATERIALS',  "All Materials",          "All items across all materials"),
        ],
        default='ALL_ACTIVE_MAT',
    )

    find: StringProperty(
        name="Find",
        description="Regex pattern to match in the suffix",
        default="",
    )

    replace: StringProperty(
        name="Replace",
        description="Replacement string. Supports backreferences like \\1",
        default="",
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

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode")
        layout.prop(self, "find")
        layout.prop(self, "replace")

    def execute(self, context) -> set:
        if not self.find:
            self.report({'WARNING'}, "Find pattern is empty")
            return {'CANCELLED'}
        try:
            pattern = re.compile(self.find)
        except re.error as e:
            self.report({'ERROR'}, f"Invalid regex: {e}")
            return {'CANCELLED'}

        materials = self._get_materials(context)
        if not materials:
            self.report({'WARNING'}, "No valid material(s) found")
            return {'CANCELLED'}

        renamed = 0
        for mat in materials:
            for item in self._get_items(mat):
                new_name, count = pattern.subn(self.replace, item.name)
                if count and new_name != item.name:
                    item.name = new_name
                    renamed += 1

        if renamed == 0:
            self.report({'WARNING'}, "No suffixes matched")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Renamed {renamed} suffix(es)")
        return {'FINISHED'}


def _swap_socket_items(self, context):
    """Output sockets of the active item's node, keyed by name so the same
    choice maps onto every item sharing that node type."""
    mat = _resolve_material(context, self.material_name)
    if not mat:
        return [('NONE', 'None', '')]
    kt = mat.kitsunetools
    idx = kt.node_baker_list_index
    if not (0 <= idx < len(kt.node_baker_list)):
        return [('NONE', 'None', '')]
    node = kt.node_baker_list[idx].get_node()
    if not node or not getattr(node, "outputs", None):
        return [('NONE', 'None', '')]
    return [(o.name, f"{o.name} [{o.type}]", "") for o in node.outputs]


class NODE_OT_node_bake_swap_output(Operator):
    bl_idname = "node.node_bake_swap_output"
    bl_label = "Swap Output"
    bl_description = "Switch the selected output on every item sharing the active item's node type, e.g. MRAO -> Exponent"
    bl_options = {'UNDO'}

    material_name: StringProperty(default="")

    all_materials: BoolProperty(
        name="All Materials",
        description="Apply across every material, not just the active one",
        default=False,
    )
    from_socket: EnumProperty(name="From", items=_swap_socket_items)
    to_socket: EnumProperty(name="To", items=_swap_socket_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "all_materials")
        layout.prop(self, "from_socket")
        layout.prop(self, "to_socket")

    def execute(self, context) -> set:
        if self.from_socket == 'NONE' or self.to_socket == 'NONE':
            self.report({'WARNING'}, "No valid sockets to swap")
            return {'CANCELLED'}
        if self.from_socket == self.to_socket:
            self.report({'WARNING'}, "From and To are the same")
            return {'CANCELLED'}

        if self.all_materials:
            materials = [m for m in bpy.data.materials if m.use_nodes and m.kitsunetools.node_baker_list]
        else:
            mat = _resolve_material(context, self.material_name)
            materials = [mat] if mat else []
        if not materials:
            self.report({'WARNING'}, "No valid material(s) found")
            return {'CANCELLED'}

        swapped = 0
        for mat in materials:
            for item in mat.kitsunetools.node_baker_list:
                node = item.get_node()
                if not node:
                    continue
                names = [o.name for o in node.outputs]
                if self.to_socket not in names:
                    continue
                cur = int(item.socket_index) if item.socket_index.isdigit() else -1
                if 0 <= cur < len(names) and names[cur] == self.from_socket:
                    item.socket_index = str(names.index(self.to_socket))
                    swapped += 1

        if swapped == 0:
            self.report({'WARNING'}, "No items matched")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Swapped output on {swapped} item(s)")
        return {'FINISHED'}


class NODE_OT_node_bake_set_alpha(Operator):
    bl_idname = "node.node_bake_set_alpha"
    bl_label = "Set Alpha Channel"
    bl_description = "Enable or disable the alpha pass on items whose suffix matches, and choose which output feeds the alpha"
    bl_options = {'UNDO'}

    material_name: StringProperty(default="")

    all_materials: BoolProperty(
        name="All Materials",
        description="Apply across every material, not just the active one",
        default=False,
    )
    match: StringProperty(
        name="Suffix Contains",
        description="Only affect items whose suffix contains this text (case-insensitive). Empty matches all items",
        default="",
    )
    action: EnumProperty(
        name="Action",
        items=[
            ('ENABLE',  "Enable",  "Turn the alpha pass on and set its output socket"),
            ('DISABLE', "Disable", "Turn the alpha pass off"),
        ],
        default='ENABLE',
    )
    alpha_socket: EnumProperty(name="Alpha Output", items=_swap_socket_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "all_materials")
        layout.prop(self, "match")
        layout.prop(self, "action")
        if self.action == 'ENABLE':
            layout.prop(self, "alpha_socket")

    def execute(self, context) -> set:
        if self.all_materials:
            materials = [m for m in bpy.data.materials if m.use_nodes and m.kitsunetools.node_baker_list]
        else:
            mat = _resolve_material(context, self.material_name)
            materials = [mat] if mat else []
        if not materials:
            self.report({'WARNING'}, "No valid material(s) found")
            return {'CANCELLED'}

        needle = self.match.lower()
        enable = self.action == 'ENABLE'
        changed = 0
        for mat in materials:
            for item in mat.kitsunetools.node_baker_list:
                if needle and needle not in item.name.lower():
                    continue
                if enable:
                    node = item.get_node()
                    names = [o.name for o in node.outputs] if node else []
                    if self.alpha_socket not in names:
                        continue
                    item.has_alpha_channel = True
                    item.alpha_socket_index = str(names.index(self.alpha_socket))
                else:
                    item.has_alpha_channel = False
                changed += 1

        if changed == 0:
            self.report({'WARNING'}, "No items matched")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Updated alpha on {changed} item(s)")
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
        nodes = mat.node_tree.nodes if mat.node_tree else None

        pasted = skipped = 0
        for d in _clipboard:
            # Socket enum items resolve from the item's node, so pasting an item
            # whose node is absent on this material would fail to set the enum.
            if not nodes or not nodes.get(d.get("node_name", "")):
                skipped += 1
                continue
            item = baker_list.add()
            _dict_to_item(d, item)
            pasted += 1

        if pasted == 0:
            self.report({'ERROR'}, f"Pasted nothing, {skipped} item(s) skipped (node not found on '{mat.name}')")
            return {'CANCELLED'}

        mat.kitsunetools.node_baker_list_index = len(baker_list) - 1
        msg = f"Pasted {pasted} item(s)"
        if skipped:
            msg += f", skipped {skipped} (node not found)"
        self.report({'WARNING'} if skipped else {'INFO'}, msg)
        return {'FINISHED'}