import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy.types import SpaceView3D

_draw_handle = None

# Per-plane colours [fill, outline] — blue for first bone, orange for second
_PLANE_COLORS = [
    ((0.20, 0.50, 1.00, 0.18), (0.20, 0.55, 1.00, 0.90)),   # blue
    ((1.00, 0.55, 0.15, 0.18), (1.00, 0.60, 0.15, 0.90)),   # orange
]


def _subtree_extent(arm, starts) -> float:
    """Max axis extent across each starting-bone subtree measured individually.

    Each subtree (L arm, R arm) is measured separately so the combined L-to-R
    span never inflates the result.  Only deform bones are counted so IK
    targets, pole vectors and other non-skinning helpers are ignored.
    """
    def _walk(bone):
        yield bone
        for child in bone.children:
            yield from _walk(child)

    mw = arm.matrix_world
    best = 0.1
    for name in starts:
        root = arm.data.bones.get(name)
        if root is None:
            continue
        pts = [
            mw @ b.head_local
            for b in _walk(root) if b.use_deform
        ] + [
            mw @ b.tail_local
            for b in _walk(root) if b.use_deform
        ]
        if len(pts) < 2:
            continue
        ext = max(
            max(p.x for p in pts) - min(p.x for p in pts),
            max(p.y for p in pts) - min(p.y for p in pts),
            max(p.z for p in pts) - min(p.z for p in pts),
            0.1,
        )
        best = max(best, ext)
    return best


def register_preview(op) -> None:
    global _draw_handle
    unregister_preview()
    _draw_handle = SpaceView3D.draw_handler_add(_draw_callback, (op,), 'WINDOW', 'POST_VIEW')


def unregister_preview() -> None:
    global _draw_handle
    if _draw_handle is not None:
        try:
            SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None


def _draw_callback(op) -> None:
    try:
        if not getattr(op, 'bisect_use_bisect', False):
            return

        arm    = getattr(op, '_fpa_arm', None)
        starts = getattr(op, '_fpa_starts', [])
        axis   = getattr(op, 'bisect_axis', 'Z')
        offset = getattr(op, 'bisect_offset', 0.0)

        if arm is None or not arm.data or not starts:
            return

        # Scale all visual elements to the arm subtree only (starting bones + descendants)
        scale      = _subtree_extent(arm, starts)
        plane_size = scale * 0.75   # half-size of the plane quad
        arrow_len  = scale * 0.30   # stem length of the kept-direction arrow
        arrowhead  = scale * 0.07   # barb size

        # One bisect plane per starting bone — same opposite-sides logic as _run().
        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis]
        base_no = Vector((0.0, 0.0, 0.0))
        base_no[axis_index] = 1.0
        heads = [arm.matrix_world @ arm.data.bones[s].head_local
                 for s in starts if s in arm.data.bones]
        if not heads:
            return
        positions = [h[axis_index] for h in heads]
        opposite_sides = (len(positions) > 1
                          and min(positions) < -1e-4
                          and max(positions) > 1e-4)
        planes = []
        for h in heads:
            no = -base_no.copy() if (opposite_sides and h[axis_index] > 1e-4) else base_no.copy()
            planes.append((h + no * offset, no))

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(False)
        shader.bind()

        for i, (plane_co, plane_no) in enumerate(planes):
            fill_color, line_color = _PLANE_COLORS[i % len(_PLANE_COLORS)]

            # Tangent basis for the plane quad
            arbitrary = Vector((0.0, 0.0, 1.0))
            if abs(plane_no.dot(arbitrary)) > 0.99:
                arbitrary = Vector((1.0, 0.0, 0.0))
            t1 = plane_no.cross(arbitrary).normalized()
            t2 = plane_no.cross(t1).normalized()

            # Semi-transparent filled quad
            sz = plane_size
            c = [
                plane_co + ( t1 + t2) * sz,
                plane_co + (-t1 + t2) * sz,
                plane_co + (-t1 - t2) * sz,
                plane_co + ( t1 - t2) * sz,
            ]
            tri_verts = [tuple(c[0]), tuple(c[1]), tuple(c[2]),
                         tuple(c[0]), tuple(c[2]), tuple(c[3])]
            shader.uniform_float("color", fill_color)
            batch_for_shader(shader, 'TRIS', {"pos": tri_verts}).draw(shader)

            # Outline
            edge_verts = [tuple(c[0]), tuple(c[1]),
                          tuple(c[1]), tuple(c[2]),
                          tuple(c[2]), tuple(c[3]),
                          tuple(c[3]), tuple(c[0])]
            shader.uniform_float("color", line_color)
            batch_for_shader(shader, 'LINES', {"pos": edge_verts}).draw(shader)

            # Arrow pointing toward the kept side (−normal direction)
            kept      = -plane_no
            tip       = plane_co + kept * arrow_len
            barb_base = tip - kept * arrowhead
            arrow_verts = [
                tuple(plane_co), tuple(tip),
                tuple(tip), tuple(barb_base + t1 * arrowhead),
                tuple(tip), tuple(barb_base - t1 * arrowhead),
                tuple(tip), tuple(barb_base + t2 * arrowhead),
                tuple(tip), tuple(barb_base - t2 * arrowhead),
            ]
            batch_for_shader(shader, 'LINES', {"pos": arrow_verts}).draw(shader)

        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')

    except Exception:
        pass
