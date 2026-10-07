import bpy
import bmesh
import numpy as np
from bpy.types import Object, Action, Mesh, Material
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

def clean_unused_shapekeys(ob: Object, threshold: float = 0.005) -> list[str]:
    """
    Remove shape keys that don't meaningfully deform any vertices beyond the threshold.
    Threshold is in world space and automatically adjusted for unapplied object scale.
    """
    if not ob or ob.type != 'MESH':
        return []

    shape_keys = ob.data.shape_keys
    if not shape_keys or not hasattr(shape_keys, 'key_blocks') or len(shape_keys.key_blocks) == 0:
        return []

    avg_scale = (abs(ob.scale.x) + abs(ob.scale.y) + abs(ob.scale.z)) / 3.0
    local_threshold = threshold / avg_scale if avg_scale > 0 else threshold

    basis = shape_keys.key_blocks[0]
    basis_coords = [v.co.copy() for v in basis.data]

    removed = []
    for key in list(shape_keys.key_blocks)[1:]:
        key_name = key.name
        max_delta = round(max((v.co - basis_coords[i]).length for i, v in enumerate(key.data)), 6)

        if max_delta <= local_threshold:
            removed.append(key_name)
            ob.shape_key_remove(key)

    if removed:
        print(f"[ShapeKey] '{ob.name}': removed {len(removed)} unused keys: {', '.join(removed)}")

    if len(shape_keys.key_blocks) == 1:
        basis_name = basis.name
        ob.shape_key_remove(basis)
        print(f"[ShapeKey] '{ob.name}': removed sole basis '{basis_name}'")
        removed.append(basis_name)

    return removed

#
#   SHAPE KEYS TO BONES
#

def read_shapekey_deltas(ob: Object, include_muted: bool = False, space: Matrix | None = None):
    """Return (rest (V,3), deltas (K,V,3), key names) of every non-basis shape key, optionally transformed by space."""
    key_blocks = ob.data.shape_keys.key_blocks
    count = len(ob.data.vertices)

    def coords(kb):
        co = np.empty(count * 3, dtype=np.float64)
        kb.data.foreach_get('co', co)
        return co.reshape(-1, 3)

    rest = coords(key_blocks[0])
    names, deltas = [], []
    for kb in key_blocks[1:]:
        if kb.mute and not include_muted:
            continue
        names.append(kb.name)
        deltas.append(coords(kb) - coords(kb.relative_key))

    deltas = np.array(deltas).reshape(len(names), count, 3)
    if space is not None:
        m = np.array(space)
        rest = rest @ m[:3, :3].T + m[:3, 3]
        deltas = deltas @ m[:3, :3].T
    return rest, deltas, names


def _kmeans(X: np.ndarray, k: int, rng: np.random.Generator, iterations: int = 40) -> np.ndarray:
    n = len(X)
    centers = np.empty((k, X.shape[1]))
    centers[0] = X[rng.integers(n)]
    d2 = ((X - centers[0]) ** 2).sum(1)
    for i in range(1, k):
        total = d2.sum()
        centers[i] = X[rng.choice(n, p=d2 / total) if total > 0 else rng.integers(n)]
        d2 = np.minimum(d2, ((X - centers[i]) ** 2).sum(1))

    xx = (X * X).sum(1)[:, None]
    labels = np.full(n, -1)
    for _ in range(iterations):
        dist = xx - 2.0 * X @ centers.T + (centers * centers).sum(1)[None]
        new_labels = dist.argmin(1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        counts = np.bincount(labels, minlength=k)
        onehot = np.zeros((k, n))
        onehot[labels, np.arange(n)] = 1.0
        sums = onehot @ X
        for c in np.flatnonzero(counts == 0):
            far = dist[np.arange(n), labels].argmax()
            sums[c], counts[c] = X[far], 1
            labels[far] = c
        centers = sums / counts[:, None]
    return labels


def _project_capped_simplex(Y: np.ndarray) -> np.ndarray:
    """Row-wise projection onto {w >= 0, sum(w) <= 1}."""
    W = np.maximum(Y, 0.0)
    over = W.sum(1) > 1.0
    if over.any():
        y = Y[over]
        u = -np.sort(-y, axis=1)
        css = np.cumsum(u, axis=1) - 1.0
        cond = u - css / np.arange(1, y.shape[1] + 1) > 0
        rho = y.shape[1] - 1 - np.argmax(cond[:, ::-1], axis=1)
        theta = css[np.arange(len(y)), rho] / (rho + 1)
        W[over] = np.maximum(y - theta[:, None], 0.0)
    return W


def _solve_weights(G: np.ndarray, b: np.ndarray, mask: np.ndarray, iterations: int = 150) -> np.ndarray:
    """Batched FISTA for min |A w - d|^2 with w >= 0, sum(w) <= 1, given G = A^T A (N,c,c) and b = A^T d (N,c)."""
    step = 1.0 / np.maximum(np.linalg.eigvalsh(G)[:, -1], 1e-12)
    w = np.zeros_like(b)
    z, t = w.copy(), 1.0
    for _ in range(iterations):
        grad = np.einsum('nij,nj->ni', G, z) - b
        w_next = _project_capped_simplex(np.where(mask, z - step[:, None] * grad, -1e9))
        t_next = 0.5 * (1.0 + np.sqrt(1.0 + 4.0 * t * t))
        z = w_next + ((t - 1.0) / t_next) * (w_next - w)
        w, t = w_next, t_next
    return w


def _solve_translations(W: np.ndarray, D: np.ndarray) -> np.ndarray:
    WtW = W.T @ W
    ridge = 1e-6 * max(np.trace(WtW) / len(WtW), 1e-12)
    return np.linalg.solve(WtW + ridge * np.eye(len(WtW)), W.T @ D)


def _limit_influences(W: np.ndarray, max_influences: int) -> np.ndarray:
    if W.shape[1] > max_influences:
        drop = np.argsort(W, axis=1)[:, :-max_influences]
        np.put_along_axis(W, drop, 0.0, axis=1)
    total = W.sum(1, keepdims=True)
    return np.where(total > 1.0, W / np.maximum(total, 1e-12), W)


def solve_shapekey_bones(rest: np.ndarray, deltas: np.ndarray, edges: np.ndarray,
                         bone_count: int = 24, max_influences: int = 4,
                         motion_threshold: float = 0.05, spatial_weight: float = 1.0,
                         smooth_iterations: int = 1, solver_iterations: int = 3, seed: int = 0) -> dict | None:
    """
    Fit translation-only bones whose linear blend reproduces the shape key deltas.
    Weight left over per vertex (1 - sum) belongs to the static parent bone.
    """
    K, V, _ = deltas.shape
    mag = np.linalg.norm(deltas, axis=2)
    key_max = mag.max(axis=1)
    rel = mag / np.maximum(key_max, 1e-12)[:, None]
    moving = np.flatnonzero((rel > motion_threshold).any(axis=0) & (mag.max(axis=0) > 1e-9))
    bone_count = min(bone_count, len(moving))
    if bone_count < 1:
        return None

    P = rest[moving]
    D = deltas[:, moving, :].transpose(1, 0, 2).reshape(len(moving), K * 3)

    # Keys are normalized so small shapes (pupils, eyelids) weigh as much as large ones in clustering
    motion = (deltas[:, moving, :] / np.maximum(key_max, 1e-12)[:, None, None]).transpose(1, 0, 2).reshape(len(moving), K * 3)
    pos = P - P.mean(0)
    pos_scale = spatial_weight * np.sqrt((motion ** 2).sum(1).mean() / max((pos ** 2).sum(1).mean(), 1e-12))
    labels = _kmeans(np.hstack([motion, pos * pos_scale]), bone_count, np.random.default_rng(seed))

    W = np.zeros((len(moving), bone_count))
    W[np.arange(len(moving)), labels] = 1.0
    strength = np.linalg.norm(D, axis=1)

    local = np.full(V, -1)
    local[moving] = np.arange(len(moving))
    e = local[edges] if len(edges) else np.empty((0, 2), dtype=int)
    e = e[(e >= 0).any(1)]

    def bone_heads(W):
        mw = W * strength[:, None]
        return (mw.T @ P) / np.maximum(mw.sum(0), 1e-12)[:, None]

    candidates = min(bone_count, max(2 * max_influences, 8))
    for _ in range(solver_iterations):
        T = _solve_translations(W, D)
        dist = ((P[:, None, :] - bone_heads(W)[None]) ** 2).sum(2)
        cand = np.argsort(dist, axis=1)[:, :candidates]

        GT = T @ T.T
        G = GT[cand[:, :, None], cand[:, None, :]]
        b = np.take_along_axis(D @ T.T, cand, axis=1)
        w = _solve_weights(G, b, np.ones_like(b, dtype=bool))
        if candidates > max_influences:
            keep = np.zeros_like(w, dtype=bool)
            np.put_along_axis(keep, np.argsort(w, axis=1)[:, -max_influences:], True, axis=1)
            w = _solve_weights(G, b, keep)

        W = np.zeros_like(W)
        np.put_along_axis(W, cand, w, axis=1)

        # Neighbours outside the moving region count as zero, giving a soft falloff at the border
        for _ in range(smooth_iterations):
            acc = np.zeros_like(W)
            deg = np.zeros(len(W))
            for a, c in ((0, 1), (1, 0)):
                src, dst = e[:, a], e[:, c]
                ok = dst >= 0
                vals = np.where((src >= 0)[:, None], W[np.maximum(src, 0)], 0.0)
                np.add.at(acc, dst[ok], vals[ok])
                np.add.at(deg, dst[ok], 1.0)
            has = deg > 0
            W[has] = 0.5 * W[has] + 0.5 * acc[has] / deg[has, None]
            W = _limit_influences(W, max_influences)

    W = W[:, W.max(0) > 1e-4]
    T = _solve_translations(W, D)
    heads = bone_heads(W)
    fit = 1.0 - np.linalg.norm(W @ T - D) / max(np.linalg.norm(D), 1e-12)

    T = T.reshape(-1, K, 3).transpose(1, 0, 2)
    dominant = (np.linalg.norm(T, axis=2) / np.maximum(key_max, 1e-12)[:, None]).argmax(0)

    return {
        'indices': moving,
        'weights': W,
        'translations': T,
        'heads': heads,
        'dominant_key': dominant,
        'fit': fit,
    }


def assign_shapekey_bone_weights(ob: Object, indices: np.ndarray, weights: np.ndarray,
                                 bone_names: list[str], remainder_group: str, deform_groups: set[str]) -> None:
    """
    Write face bone weights and scale existing deform weights to fill the remainder (1 - face sum).
    Vertices without deform weights get the remainder on remainder_group.
    """
    vgroups = ob.vertex_groups
    face_groups = [vgroups.get(n) or vgroups.new(name=n) for n in bone_names]
    remainder_vg = vgroups.get(remainder_group) or vgroups.new(name=remainder_group)
    face_index = {vg.index for vg in face_groups}
    deform_index = {vg.index for vg in vgroups if vg.name in deform_groups and vg.index not in face_index}
    group_by_index = {vg.index: vg for vg in vgroups}

    face_sum = np.zeros(len(ob.data.vertices))
    face_sum[indices] = weights.sum(1)

    for v in ob.data.vertices:
        remainder = 1.0 - face_sum[v.index]
        existing = [(g.group, g.weight) for g in v.groups if g.group in deform_index and g.weight > 0.0]
        total = sum(w for _, w in existing)
        if total > 0.0:
            if face_sum[v.index] > 0.0:
                for gi, w in existing:
                    group_by_index[gi].add([v.index], w * remainder / total, 'REPLACE')
        elif remainder > 0.0:
            remainder_vg.add([v.index], remainder, 'REPLACE')

    for row, vi in zip(weights, indices):
        for b in np.flatnonzero(row > 1e-4):
            face_groups[b].add([int(vi)], float(row[b]), 'REPLACE')


def write_shapekey_pose_action(arm_ob: Object, name: str, bone_names: list[str],
                               key_names: list[str], locations: np.ndarray) -> Action:
    """
    Build an action with the rest pose on frame 0 and each shape key on its own frame, labelled by a pose marker.
    locations is (K, B, 3) in each bone's local rest space. Assigned only when the armature has no action.
    """
    action = bpy.data.actions.new(name=name)
    action.use_fake_user = True
    slot = action.slots.new(id_type='OBJECT', name=arm_ob.name)
    strip = action.layers.new(name="Layer").strips.new(type='KEYFRAME')
    channelbag = strip.channelbags.new(slot=slot)

    frames = np.arange(len(key_names) + 1, dtype=np.float64)
    for b, bone_name in enumerate(bone_names):
        group = channelbag.groups.new(name=bone_name)
        path = f'pose.bones["{bpy.utils.escape_identifier(bone_name)}"].location'
        for axis in range(3):
            fc = channelbag.fcurves.new(data_path=path, index=axis)
            fc.group = group
            values = np.concatenate([[0.0], locations[:, b, axis]])
            fc.keyframe_points.add(len(frames))
            fc.keyframe_points.foreach_set('co', np.column_stack([frames, values]).ravel())
            for kp in fc.keyframe_points:
                kp.interpolation = 'CONSTANT'
            fc.update()

    for i, key_name in enumerate(key_names, start=1):
        action.pose_markers.new(key_name).frame = i

    anim = arm_ob.animation_data or arm_ob.animation_data_create()
    if anim.action is None:
        anim.action = action
        anim.action_slot = slot
    return action


def _edit_or_mesh_bmesh(ob: Object) -> bmesh.types.BMesh:
    if ob.mode == 'EDIT':
        return bmesh.from_edit_mesh(ob.data).copy()
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    return bm


def create_hair_shadow_mesh(hair_ob: Object, target_ob: Object, name: str, direction: Vector | None,
                            drop: float = 0.0, offset: float = 0.0005, search_back: float = 0.02,
                            subdivisions: int = 1, single_layer: bool = True,
                            copy_weights: bool = True) -> Mesh | None:
    """Flatten the selected edit-mode faces of hair_ob onto target_ob's surface as a new mesh.
    direction is a world-space projection vector, None snaps to the nearest surface point.
    The result is in target_ob's local space with target_ob's vertex group indices."""
    src = bmesh.from_edit_mesh(hair_ob.data).copy()
    bmesh.ops.delete(src, geom=[f for f in src.faces if not f.select], context='FACES')
    bmesh.ops.delete(src, geom=[e for e in src.edges if not e.link_faces], context='EDGES')
    bmesh.ops.delete(src, geom=[v for v in src.verts if not v.link_faces], context='VERTS')
    if not src.faces:
        src.free()
        return None
    for layer in list(src.verts.layers.shape):
        src.verts.layers.shape.remove(layer)
    src.transform(hair_ob.matrix_world)
    src.normal_update()

    tgt = _edit_or_mesh_bmesh(target_ob)
    tgt.transform(target_ob.matrix_world)
    tgt.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(tgt)
    drop_vec = Vector((0.0, 0.0, -drop))

    def project(co: Vector):
        p = co + drop_vec
        if direction is not None:
            hit = tree.ray_cast(p - direction * search_back, direction)
            if hit[0] is not None:
                return hit
        return tree.find_nearest(p)

    # Bangs are often double-sided strands, keeping only faces pointing away from the skin
    # avoids stacked shadow layers once everything is flattened
    if single_layer:
        inner = []
        for f in src.faces:
            hit = project(f.calc_center_median())
            if hit[0] is not None and f.normal.dot(hit[1]) <= 0.0:
                inner.append(f)
        if len(inner) < len(src.faces):
            bmesh.ops.delete(src, geom=inner, context='FACES')

    if subdivisions > 0:
        bmesh.ops.subdivide_edges(src, edges=src.edges[:], cuts=subdivisions, use_grid_fill=True)

    t_deform = tgt.verts.layers.deform.active
    s_deform = src.verts.layers.deform.verify()
    hit_normals = {}
    for v in src.verts:
        loc, normal, face_index, _ = project(v.co)
        dvert = v[s_deform]
        dvert.clear()
        if loc is None:
            continue
        v.co = loc + normal * offset
        hit_normals[v] = normal
        if copy_weights and t_deform is not None:
            nearest = min(tgt.faces[face_index].verts, key=lambda tv: (tv.co - loc).length_squared)
            for group_index, weight in nearest[t_deform].items():
                dvert[group_index] = weight

    src.normal_update()
    flip = []
    for f in src.faces:
        f.material_index = 0
        avg = Vector()
        for v in f.verts:
            avg += hit_normals.get(v, f.normal)
        if avg.dot(f.normal) < 0.0:
            flip.append(f)
    if flip:
        bmesh.ops.reverse_faces(src, faces=flip)

    src.transform(target_ob.matrix_world.inverted())
    mesh = bpy.data.meshes.new(name)
    src.to_mesh(mesh)
    src.free()
    tgt.free()
    return mesh


def get_hair_shadow_material(name: str, color) -> Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if mat.node_tree is None:
            mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Alpha"].default_value = color[3]
        bsdf.inputs["Roughness"].default_value = 1.0
    mat.diffuse_color = color
    mat.surface_render_method = 'BLENDED'
    mat.use_backface_culling = True
    return mat
