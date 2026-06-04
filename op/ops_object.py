import bpy
from bpy.types import Operator, Context
from bpy.props import BoolProperty

from ..utils.utils_object import (
    TRANSFORM_APPLY_TYPES,
    get_constraint_target_world,
    sort_objects_parents_first,
    gather_transform_apply_context,
    find_transform_driver_targets,
    get_apply_anchor_collection,
    create_apply_anchor,
    attach_apply_anchor,
)


class OBJECT_OT_ApplyTransformsSafe(Operator):
    """Apply Location/Rotation/Scale like Object > Apply, but re-solve parenting, Child Of
    constraints and other origin-space dependencies so the rig keeps working afterwards"""
    bl_idname = "kitsunetools.apply_transforms_safe"
    bl_label = "Apply Transforms (Safe)"
    bl_options = {'REGISTER', 'UNDO'}

    use_location: BoolProperty(name="Location", default=True)
    use_rotation: BoolProperty(name="Rotation", default=True)
    use_scale: BoolProperty(name="Scale", default=True)

    experimental_redirect: BoolProperty(
        name="Experimental: Redirect Dependencies",
        description="Create an anchor empty at each object's old origin and re-point constraints "
                    "and drivers to it, so they keep reading the pre-apply transform. Universal but "
                    "adds helper objects to a 'KitsuneTools Apply Anchors' collection",
        default=False,
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return bool(
            context.mode == 'OBJECT'
            and any(ob.type in TRANSFORM_APPLY_TYPES for ob in context.selected_objects)
        )

    def draw(self, context: Context) -> None:
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, 'use_location')
        col.prop(self, 'use_rotation')
        col.prop(self, 'use_scale')
        layout.separator()
        layout.prop(self, 'experimental_redirect')

    def execute(self, context: Context) -> set:
        if not (self.use_location or self.use_rotation or self.use_scale):
            self.report({'WARNING'}, "No transform components selected to apply")
            return {'CANCELLED'}

        applied = [
            ob for ob in context.selected_objects
            if ob.type in TRANSFORM_APPLY_TYPES and ob.name in context.view_layer.objects
        ]
        if not applied:
            self.report({'WARNING'}, "No applicable objects selected")
            return {'CANCELLED'}

        context.view_layer.update()
        applied_set = set(applied)

        deps = gather_transform_apply_context(applied)
        driver_targets = find_transform_driver_targets(applied)
        origin_refs = deps['origin_refs']

        # Snapshot world matrices BEFORE applying. Apply Transform is visually neutral, so every
        # object's world matrix is the target we restore dependents (and place anchors) at.
        applied_world = {ob.name: ob.matrix_world.copy() for ob in applied}
        children = deps['children']
        child_world = {c.name: c.matrix_world.copy() for c in children}

        anchor_map = {}
        redirected_constraints = 0
        redirected_drivers = 0
        resolved_childof = 0

        if self.experimental_redirect:
            anchor_map = self._build_anchors(context, applied_set, applied_world,
                                             origin_refs, driver_targets)
            redirected_constraints = self._redirect_constraints(origin_refs, anchor_map)
            redirected_drivers = self._redirect_drivers(driver_targets, anchor_map)
        else:
            # Old target world for each Child Of so its inverse can be re-solved after apply.
            childof_resolve = [
                (con, target, get_constraint_target_world(target, ""))
                for (_owner, _pbone, con, attr, target) in origin_refs
                if con.type == 'CHILD_OF' and attr == 'target'
            ]

        # Apply parents before children: once a parent is applied its world changes, so a selected
        # child is restored to its original world right before being applied itself.
        errors = []
        applied_ok = 0
        for ob in sort_objects_parents_first(applied):
            try:
                ob.matrix_world = applied_world[ob.name]
            except Exception:
                pass
            context.view_layer.update()
            try:
                with context.temp_override(
                    active_object=ob, object=ob,
                    selected_objects=[ob], selected_editable_objects=[ob],
                ):
                    bpy.ops.object.transform_apply(
                        location=self.use_location,
                        rotation=self.use_rotation,
                        scale=self.use_scale,
                    )
                applied_ok += 1
            except RuntimeError as e:
                errors.append(f"{ob.name}: {e}")
            context.view_layer.update()

        if self.experimental_redirect:
            # Parent each anchor to its object so it tracks the object's visual from now on.
            for ob, anchor in anchor_map.items():
                attach_apply_anchor(anchor, ob, applied_world[ob.name])
        else:
            # Re-solve Child Of inverses: inv_new = T_new⁻¹ @ T_old @ inv_old keeps the owner
            # exactly where it was, regardless of how the target's origin moved.
            for con, target, t_old in childof_resolve:
                try:
                    t_new = get_constraint_target_world(target, "")
                    con.inverse_matrix = t_new.inverted() @ t_old @ con.inverse_matrix
                    resolved_childof += 1
                except Exception:
                    pass

        # Restore non-applied children (object/bone/vertex parented) to their original world.
        restored_children = 0
        for c in children:
            if c.name not in bpy.data.objects:
                continue
            try:
                c.matrix_world = child_world[c.name]
                restored_children += 1
            except Exception:
                pass
        context.view_layer.update()

        self._report_summary(applied_ok, restored_children, resolved_childof, redirected_constraints,
                             redirected_drivers, anchor_map, deps, driver_targets, errors)
        return {'FINISHED'} if applied_ok else {'CANCELLED'}

    def _build_anchors(self, context, applied_set, applied_world, origin_refs, driver_targets) -> dict:
        needed = {target for (_o, _p, _c, _a, target) in origin_refs}
        needed |= {applied_obj for (_t, applied_obj, _l, _d) in driver_targets}
        needed &= applied_set
        if not needed:
            return {}
        collection = get_apply_anchor_collection()
        anchor_map = {}
        for ob in needed:
            anchor_map[ob] = create_apply_anchor(ob, applied_world[ob.name], collection)
        context.view_layer.update()
        return anchor_map

    def _redirect_constraints(self, origin_refs, anchor_map) -> int:
        count = 0
        for _owner, _pbone, con, attr, target in origin_refs:
            anchor = anchor_map.get(target)
            if anchor is None:
                continue
            setattr(con, attr, anchor)
            sub_attr = 'subtarget' if attr == 'target' else 'pole_subtarget'
            if hasattr(con, sub_attr):
                setattr(con, sub_attr, "")
            count += 1
        return count

    def _redirect_drivers(self, driver_targets, anchor_map) -> int:
        count = 0
        for tgt, applied_obj, _label, _dp in driver_targets:
            anchor = anchor_map.get(applied_obj)
            if anchor is None:
                continue
            try:
                tgt.id = anchor
                count += 1
            except Exception:
                pass
        return count

    def _report_summary(self, applied_ok, restored_children, resolved_childof, redirected_constraints,
                        redirected_drivers, anchor_map, deps, driver_targets, errors) -> None:
        parts = [f"Applied transforms to {applied_ok} object(s)"]
        if restored_children:
            parts.append(f"{restored_children} parented object(s) preserved")
        if resolved_childof:
            parts.append(f"{resolved_childof} Child Of constraint(s) re-solved")
        if self.experimental_redirect and anchor_map:
            parts.append(f"{len(anchor_map)} anchor(s) created")
            if redirected_constraints:
                parts.append(f"{redirected_constraints} constraint(s) redirected")
            if redirected_drivers:
                parts.append(f"{redirected_drivers} driver(s) redirected")
        self.report({'INFO'}, "; ".join(parts))

        # Unrecognised constraint types are warned in both modes (not auto-corrected).
        for owner_label, con_name, con_type, target_name in deps['warn_refs']:
            self.report({'WARNING'},
                        f"Constraint '{con_name}' ({con_type}) on '{owner_label}' targets "
                        f"'{target_name}' : unsupported type, verify it manually")

        if not self.experimental_redirect:
            for _owner, _pbone, con, attr, target in deps['origin_refs']:
                if con.type == 'CHILD_OF' and attr == 'target':
                    continue
                self.report({'WARNING'},
                            f"Constraint '{con.name}' ({con.type}) targets '{target.name}' : enable "
                            f"'Redirect Dependencies' to auto-correct, or adjust it manually")
            for _tgt, _obj, owner_label, data_path in driver_targets:
                self.report({'WARNING'},
                            f"Driver on '{owner_label}' ({data_path}) reads an applied object's "
                            f"transform : enable 'Redirect Dependencies' to auto-correct")

        for mesh in deps['deformed_meshes']:
            self.report({'WARNING'},
                        f"Mesh '{mesh.name}' is deformed by an applied armature but was not selected "
                        f": select it too so it gets the same transform applied")

        for err in errors:
            self.report({'ERROR'}, f"Failed to apply: {err}")
