import array
import traceback
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

import bmesh
import bpy
import bpy_extras.mesh_utils
from mathutils import Vector
from mathutils.kdtree import KDTree

from ..constants import (
    ADDON_ID_PREFIX,
    ANALYSIS_PREPROCESS_TEXT, ANALYSIS_THRESHOLDS, BUST_LINE_MAX_ITER, COORD_ROUND_DIGITS,
    BASEBODY_TMP_OBJ_NAME, BREAST_BL_NAME_L, BREAST_BL_NAME_R, CUP_LIST,
    DEFAULT_TMP_OBJ_NAME, LINKED_FACES_THRESHOLD, MAX_RING_COUNT,
    MAX_SEARCH_COUNT, STANDARD_CUP_LIST, TASK_ANALYSIS, TIMER_INTERVAL,
    TMP_ANALYSIS_COLLECTION_NAME, TMP_COLLECTION_NAME,
)
from ..utils import (
    adjust_ang_limits, bmesh_from_mesh, clean_temp_data, copy_obj,
    deselect_all_objects, find_ancestor, find_objs, format_cm,
    get_breast_bones_map, get_collection, get_standard_cup_index,
    is_visible_in_viewport, purge_new_orphan_data, remap_breast_weights,
    restore_selection, select_and_activate, set_batch_progress,
    snapshot_data_blocks, tag_redraw, trans_vg, timeit,
)


class AnalysisStatus(Enum):
    """
    胸部网格结构复杂：可尝试删除无关内容后重新检测。
    胸部结构异常：模型本身存在问题。
    """
    OK = (0, "")
    NO_BREAST_VERTS = (1, "无法获取有效胸部网格范围！请检查顶点组中是否存在顶点。")
    NO_ISLANDS = (2, "未找到胸部网格松散块！")
    NO_BOUNDARY = (3, "未找到胸部网格松散块的边界顶点！")
    NO_BREAST_VERTS_AFTER_DELETE_INVALID = (4, "胸部网格结构复杂！移除无效顶点后，无剩余胸部网格。")
    NO_BREAST_INNER = (5, "无法获取胸部内侧点！")
    BREAST_MESH_COMPLEX = (6, "胸部网格结构复杂！请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_NIPPLE_ROOT_OVERLAP = (7, "胸部网格结构复杂！乳首与乳根位置相同。")
    BREAST_MESH_COMPLEX_NO_UPPER_BUST = (8, "胸部网格结构复杂！无法准确获取上胸围，请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_NO_UNDER_BUST = (9, "胸部网格结构复杂！无法准确获取下胸围，请删除无关内容后重试。")
    BUST_DIFF_TOO_LARGE = (10, "胸部网格结构复杂！胸围差过大，请删除无关内容后重试。")
    BUST_DIFF_NEGATIVE = (11, "胸部网格结构复杂！胸围差为负值，请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_OVERLAPPING_OPPOSITE_NORMALS = (12,
                                                        "胸部网格结构复杂！存在重叠且法向相反的面，请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_TOO_MANY_UPPER_RINGS = (13, "胸部网格结构复杂，检测到异常数量的上胸围环。请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_TOO_MANY_UNDER_RINGS = (14, "胸部网格结构复杂，检测到异常数量的下胸围环。请删除无关内容后重试。")
    BREAST_MESH_COMPLEX_NO_UPPER_RINGS = (15, "胸部网格结构复杂，未检测到上胸围环。请检查后重试。")
    BREAST_MESH_COMPLEX_NO_UNDER_RINGS = (16, "胸部网格结构复杂，未检测到下胸围环。请检查后重试。")
    BREAST_MESH_COMPLEX_UPPER_RINGS_MAX_SEARCH = (17, "胸部网格结构复杂，检测到异常数量的上胸围环。请删除无关平面后重试。")
    BREAST_MESH_COMPLEX_UNDER_RINGS_MAX_SEARCH = (18, "胸部网格结构复杂，检测到异常数量的下胸围环。请删除无关平面后重试。")
    NO_BREAST_LOWER = (19, "胸部结构异常，无法获取胸部下侧点。")
    NO_BREAST_WIDTH = (20, "胸部结构异常，无法获取胸部宽度。")
    ERROR = (99, "未知错误！")

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg


@dataclass
class AnalysisResult:
    status: AnalysisStatus = AnalysisStatus.ERROR
    msg: str = ""

    threshold: float | None = None
    nipple_co: Vector | None = None
    under_co: Vector | None = None
    inner_co: Vector | None = None
    max_x: float | None = None
    cup: str | None = None
    cup_index: int | None = None
    cup_inner: str | None = None
    cup_inner_index: int | None = None

    upper_bust: float | None = None
    under_bust: float | None = None
    bust_diff: float | None = None

    breast_x: float | None = None
    breast_y: float | None = None
    breast_z: float | None = None

    tmp_obj: bpy.types.Object | None = None
    tmp_obj_for_base_body: bpy.types.Object | None = None

    opposite_normals: bool = False
    max_weight: float | None = None


def get_co_key(co):
    return tuple(round(c, COORD_ROUND_DIGITS) for c in co)


def remove_doubles_between_rings(tmp_obj, idx_rings_map, upper_rings):
    """
    仅合并不同环上距离过近的顶点，不影响其他顶点。
    """
    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()

        # 获取所有环中顶点
        tmp_verts = [
            bm.verts[idx]
            for ring in upper_rings
            for idx in ring
        ]
        tmp_verts = sort_verts(tmp_verts)

        verts_to_merge = set()

        # 两两比较
        for i in range(len(tmp_verts)):
            v1 = tmp_verts[i]
            for j in range(i + 1, len(tmp_verts)):
                v2 = tmp_verts[j]

                # 跳过自身
                if v1.index == v2.index:
                    continue

                # 跳过同一个环
                # 若两个顶点中的任意一个属于多个环，则不认为这两个顶点属于同一个环。（例如：崩坏3的琪亚娜_薪炎之律者）
                r1 = idx_rings_map[v1.index]
                r2 = idx_rings_map[v2.index]
                if len(r1) == len(r2) == 1 and r1 == r2:
                    continue

                # 距离判断
                if (v1.co - v2.co).length <= 1e-3:
                    center = (v1.co + v2.co) * 0.5
                    v1.co = center
                    v2.co = center
                    verts_to_merge.add(v1)
                    verts_to_merge.add(v2)

        bm.to_mesh(tmp_obj.data)

        # 按距离合并
        if verts_to_merge:
            bmesh.ops.remove_doubles(
                bm,
                verts=list(verts_to_merge),
                dist=1e-6,
            )

        bm.to_mesh(tmp_obj.data)
        tmp_obj.data.update()


def get_sorted_link_edges(vert):
    """获取按边质心 z、x、y 排序后的关联边"""

    edge_centers = [
        (
            edge,
            (vert.co + edge.other_vert(vert).co) / 2
        )
        for edge in vert.link_edges
    ]

    edge_centers.sort(
        key=lambda item: (
            item[1].z,
            item[1].x,
            item[1].y,
        )
    )

    return [edge for edge, _ in edge_centers]


def sort_verts(verts):
    return sorted(
        verts,
        key=lambda v: (
            v.co.z,
            v.co.x,
            v.co.y,
        ),
    )


def process_muti_rings(tmp_obj, loc_z, ref_co, rings):
    # 合并不同环上距离过近的顶点，使不同环相连，供后续处理
    idx_rings_map = defaultdict(list)
    for ring_idx, ring in enumerate(rings):
        for v_idx in ring:
            idx_rings_map[v_idx].append(ring_idx)
    remove_doubles_between_rings(tmp_obj, idx_rings_map, rings)

    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()

        # 获取 z=loc_z 平面上的顶点
        loc_z_verts = [v for v in bm.verts if v.co.z == loc_z]
        loc_z_verts = sort_verts(loc_z_verts)
        # 获取start参考点
        start_verts = []
        # 1. 距离 nipple_co / under_co 最近的顶点
        start_verts.append(min(loc_z_verts, key=lambda v: (v.co - ref_co).length))
        # 2. 距离 nipple_co / under_co（X 取相反值）最近的顶点
        mirror_ref_co = ref_co.copy()
        mirror_ref_co.x *= -1
        start_verts.append(min(loc_z_verts, key=lambda v: (v.co - mirror_ref_co).length))
        # 3. Y 最大的顶点
        start_verts.append(max(loc_z_verts, key=lambda v: v.co.y))

        # 交叉点 -> 可能的prev集合
        x_prevs_map = defaultdict(list)
        visited = set()
        for index, start_vert in enumerate(start_verts):

            # 初始化遍历参数
            curr_visited = set()
            curr = start_vert
            prev = None

            while True:
                co_key = get_co_key(curr.co)
                curr_visited.add(co_key)

                # 获取与当前顶点相临且在z=z_max平面上的顶点
                others = set()
                for edge in get_sorted_link_edges(curr):
                    other = edge.other_vert(curr)
                    if other.co.z != loc_z:
                        continue
                    others.add(other)

                # 如果符合条件的相邻顶点数量大于2，则表示环与环之间发生交叉
                if len(others) > 2:

                    if not prev:
                        # 胸部结构复杂
                        return AnalysisStatus.BREAST_MESH_COMPLEX, None

                    # 发生交叉的前一个顶点 -> 交叉点
                    x_prevs_map[get_co_key(curr.co)].append(get_co_key(prev.co))

                    # 重置遍历参数，访问另一侧
                    curr = start_vert

                # 找下一个相邻点
                next_vert = None
                for edge in get_sorted_link_edges(curr):
                    other = edge.other_vert(curr)
                    # 排除不在z=z_max上的顶点
                    if other.co.z != loc_z:
                        continue
                    # 排除已访问过的顶点
                    if get_co_key(other.co) in curr_visited:
                        continue
                    next_vert = other
                    break

                # 达成闭环，结束遍历
                if next_vert is None:
                    visited.update(curr_visited)
                    break

                # 继续遍历
                prev = curr
                curr = next_vert

    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()

        for x_co, prev_cos in x_prevs_map.items():
            if len(prev_cos) == 2:
                prev0 = next((v for v in bm.verts if get_co_key(v.co) == get_co_key(prev_cos[0])), None)
                prev1 = next((v for v in bm.verts if get_co_key(v.co) == get_co_key(prev_cos[1])), None)
                # 胸部网格结构复杂时，可能会出现相等的情况，直接continue
                if prev0 == prev1:
                    continue
                # 添加边
                if bm.edges.get((prev0, prev1)) is None:
                    bm.edges.new((prev0, prev1))
                    bm.faces.ensure_lookup_table()
                    bm.edges.ensure_lookup_table()
                    bm.verts.ensure_lookup_table()
        x_verts = [v for v in bm.verts if get_co_key(v.co) in x_prevs_map]
        bmesh.ops.delete(bm, geom=x_verts, context='VERTS')
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        verts = [v.index for v in bm.verts if get_co_key(v.co) in visited]

        bm.to_mesh(tmp_obj.data)
        tmp_obj.data.update()

    return None, verts


def remove_z_verts_except_ring(obj, upper_ring, loc_z):
    mesh = obj.data

    with bmesh_from_mesh(mesh) as bm:
        bm.verts.ensure_lookup_table()

        keep_indices = set(upper_ring)

        remove_verts = [
            v for v in bm.verts
            if v.co.z == loc_z and v.index not in keep_indices
        ]

        if remove_verts:
            bmesh.ops.delete(bm, geom=remove_verts, context='VERTS')

        bm.to_mesh(mesh)
        mesh.update()


def further_process_for_base_body(source_objs, b_names, result, index, clean_mesh=True):
    if result.status != AnalysisStatus.OK:
        return

    if result.opposite_normals:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_OVERLAPPING_OPPOSITE_NORMALS
        result.status = status
        result.msg = status.msg
        return

    # 重置cup参数
    result.cup = None

    # todo 通用和素体方式共用参数互相影响 应修复
    # 通常情况下，z_max为乳首位置，z_min为乳根位置
    # 胸部较大而自然下垂时，乳首位置可能会小于乳根位置，因此不会continue，而是取实际的最大最小值，用于后续切割（例如Hanako_BaseBody）
    z_max = max(result.nipple_co.z, result.under_co.z)
    z_min = min(result.nipple_co.z, result.under_co.z)

    # 判断乳首与乳根是否重合（例如：莱娅_热力运动），此类情况通常出现在平胸角色
    if (result.nipple_co - result.under_co).length < 1e-6:
        result.status = AnalysisStatus.BREAST_MESH_COMPLEX_NIPPLE_ROOT_OVERLAP
        result.msg = AnalysisStatus.BREAST_MESH_COMPLEX_NIPPLE_ROOT_OVERLAP.msg
        return

    # 创建临时对象
    tmp_obj_name = f"{index}_{BASEBODY_TMP_OBJ_NAME}_{result.threshold}_None"
    tmp_col, tmp_obj, temp_mesh = create_tmp_obj(None, delete_unwanted_flag=False, source_objs=source_objs,
                                                 tmp_obj_name=tmp_obj_name, clean=clean_mesh)
    # 切两刀，获取乳首与乳根中间的部分
    crop_mesh_by_z(tmp_obj, z_min, z_max)

    # 获取乳首与乳根的位置
    nipple_z = result.nipple_co.z
    under_z = result.under_co.z

    # 合并切割面的顶点，避免水平方向距离过近的顶点带来干扰
    with bmesh_from_mesh(temp_mesh) as bm:
        upper_verts = [v for v in bm.verts if v.co.z == nipple_z]
        bmesh.ops.remove_doubles(bm, verts=upper_verts, dist=1e-5)
        under_verts = [v for v in bm.verts if v.co.z == under_z]
        bmesh.ops.remove_doubles(bm, verts=under_verts, dist=1e-5)
        bm.to_mesh(temp_mesh)

    # 保留 under_co、nipple_co 所在孤岛，删除其余孤岛
    # 此处因为是位置校验而非顶点校验，所以islands可能会多选，需要后续进一步处理
    keep_islands_between_z(tmp_obj, result.under_co, result.nipple_co)

    # 避免极端情况下，环数过多或缺失
    upper_rings, under_rings, status = get_and_check_rings(tmp_obj, nipple_z, under_z, max_ring_count=MAX_RING_COUNT)
    if status:
        result.status = status
        result.msg = status.msg
        return

    # 获取upper_ring
    # 如果upper_ring存在多个环，需通过合并的方式减少环
    if len(upper_rings) > 1:
        status, upper_ring = process_muti_rings(tmp_obj, nipple_z, result.nipple_co, upper_rings)
        # 胸部结构复杂直接返回
        if status:
            result.status = status
            result.msg = status.msg
            return
    else:
        upper_ring = upper_rings[0] if upper_rings else []
    # 首次获取 upper_ring 后，移除其它位于 nipple_z 的顶点
    remove_z_verts_except_ring(tmp_obj, upper_ring, nipple_z)

    # 获取under_rings
    under_ring = get_under_ring(tmp_obj, under_z, b_names)
    # 首次获取 under_rings 后，移除其它位于 under_z 的顶点
    remove_z_verts_except_ring(tmp_obj, under_ring, under_z)

    # 消除环的自交及凹陷点（例如 Hanako_BaseBody）
    upper_ring = get_upper_ring(tmp_obj, nipple_z)
    process_bust_line(tmp_obj, upper_ring)
    under_ring = get_under_ring(tmp_obj, under_z, b_names)
    process_bust_line(tmp_obj, under_ring)

    # 再次获取上圈和下圈
    upper_ring = get_upper_ring(tmp_obj, nipple_z)
    under_ring = get_under_ring(tmp_obj, under_z, b_names)

    # 删除圈外顶点
    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()
        keep = upper_ring | under_ring
        verts_to_delete = [v for v in bm.verts if v.index not in keep]
        bmesh.ops.delete(bm, geom=verts_to_delete, context='VERTS')

        # 删除度数 <= 1 的顶点
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        loose_verts = [v for v in bm.verts if len(v.link_edges) <= 1]
        while loose_verts:
            bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')
            loose_verts = [v for v in bm.verts if len(v.link_edges) <= 1]

        bm.to_mesh(tmp_obj.data)
        tmp_obj.data.update()

    # 再次获取上圈和下圈
    upper_ring = get_upper_ring(tmp_obj, nipple_z)
    under_ring = get_under_ring(tmp_obj, under_z, b_names)

    # 删除 upper_ring_verts 与 under_ring_verts 之间的边
    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()
        edges_to_delete = []
        for edge in bm.edges:
            v1, v2 = edge.verts
            # 一个顶点属于上圈，另一个顶点属于下圈
            if (
                    (v1.index in upper_ring and v2.index in under_ring)
                    or
                    (v2.index in upper_ring and v1.index in under_ring)
            ):
                edges_to_delete.append(edge)
        # 删除边
        if edges_to_delete:
            bmesh.ops.delete(bm, geom=edges_to_delete, context='EDGES')

        bm.to_mesh(tmp_obj.data)
        tmp_obj.data.update()

    # 如果任一上下胸围顶点数小于2，则无法构成边，直接continue
    if len(upper_ring) < 2:
        result.status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UPPER_BUST
        result.msg = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UPPER_BUST.msg
        return
    if len(under_ring) < 2:
        result.status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UNDER_BUST
        result.msg = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UNDER_BUST.msg
        return

    # 计算上胸围
    upper_ring = get_upper_ring(tmp_obj, nipple_z)
    upper_bust = get_ring_edge_length(tmp_obj, upper_ring)

    # 计算下胸围
    under_ring = get_under_ring(tmp_obj, under_z, b_names)
    under_bust = get_ring_edge_length(tmp_obj, under_ring)

    # 再次校验，增加健壮性（例如：心夏_鸭子摇滚）
    if not upper_bust:
        result.status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UPPER_BUST
        result.msg = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UPPER_BUST.msg
        return

    if not under_bust:
        result.status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UNDER_BUST
        result.msg = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UNDER_BUST.msg
        return

    # 取消顶点选中
    deselect_verts(tmp_obj)

    # 围差
    bust_diff = upper_bust - under_bust
    bust_diff_cm = bust_diff * 100

    if bust_diff_cm < 0:
        result.status = AnalysisStatus.BUST_DIFF_NEGATIVE
        result.msg = f"{AnalysisStatus.BUST_DIFF_NEGATIVE.msg}上胸围：{round(upper_bust * 100, 2)} cm 下胸围：{round(under_bust * 100, 2)} cm 胸围差：{round(bust_diff * 100, 2)} cm"
        return

    # 计算罩杯
    cup = None
    if bust_diff_cm:
        for limit, c in STANDARD_CUP_LIST:
            if bust_diff_cm < limit:
                cup = c
                break

    # 解除临时对象与源对象的关联
    tmp_obj.name = f"{index}_{BASEBODY_TMP_OBJ_NAME}_{result.threshold}_{cup if cup else 'None'}"

    # 保存结果
    result.cup = cup
    result.upper_bust = upper_bust
    result.under_bust = under_bust
    result.bust_diff = bust_diff
    result.cup_index = get_standard_cup_index(cup)
    result.tmp_obj_for_base_body = tmp_obj

    if cup == STANDARD_CUP_LIST[-1][1]:
        result.status = AnalysisStatus.BUST_DIFF_TOO_LARGE
        result.msg = f"{AnalysisStatus.BUST_DIFF_TOO_LARGE.msg}上胸围：{round(upper_bust * 100, 2)} cm 下胸围：{round(under_bust * 100, 2)} cm 胸围差：{round(bust_diff * 100, 2)} cm"
        return


def get_and_check_rings(tmp_obj, nipple_z, under_z, max_ring_count=MAX_RING_COUNT):
    """检测环数过多或缺失"""
    upper_rings = get_rings_at_z(tmp_obj, nipple_z, max_ring_count=max_ring_count)
    if upper_rings is None:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_UPPER_RINGS_MAX_SEARCH
        return None, None, status
    if len(upper_rings) >= max_ring_count:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_TOO_MANY_UPPER_RINGS
        return None, None, status
    if len(upper_rings) == 0:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UPPER_RINGS
        return None, None, status

    under_rings = get_rings_at_z(tmp_obj, under_z, max_ring_count=max_ring_count)
    if under_rings is None:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_UNDER_RINGS_MAX_SEARCH
        return None, None, status
    if len(under_rings) >= max_ring_count:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_TOO_MANY_UNDER_RINGS
        return None, None, status
    if len(under_rings) == 0:
        status = AnalysisStatus.BREAST_MESH_COMPLEX_NO_UNDER_RINGS
        return None, None, status

    return upper_rings, under_rings, None


def get_edge_key(v0, v1):
    """获取无向边的唯一 key"""
    return tuple(sorted((v0, v1)))


def get_rings_at_z(obj, z_target, max_ring_count=MAX_RING_COUNT):
    """
    获取 z = z_target 处的所有闭合环。

    注意：
    - 一个顶点可能属于多个环，一条边也可能属于多个环。
    - 通过循环边获取的环，无法保证遍历结果一定构成闭合环；同时，当多个环存在共用顶点时，会将其误判为同一个环，因此排除这种处理方式。

    返回：
        list:
            检测到的闭合环列表。
        None:
            搜索过程超过最大限制
    """

    mesh = obj.data

    # 获取目标 Z 高度上的顶点
    candidate_indices = {v.index for v in mesh.vertices if v.co.z == z_target}
    if not candidate_indices:
        return []

    # 建立无向图
    # vertex -> 相邻 vertex
    adjacency = defaultdict(set)
    for edge in mesh.edges:
        v0, v1 = edge.vertices
        if v0 not in candidate_indices or v1 not in candidate_indices:
            continue

        adjacency[v0].add(v1)
        adjacency[v1].add(v0)

    # 移除孤立顶点
    adjacency = {
        v: neighbors
        for v, neighbors in adjacency.items()
        if neighbors
    }

    if not adjacency:
        return []

    # 搜索所有简单环，允许环之间共用顶点和边
    # A────B────C
    # │    │    │
    # D────E────F
    # │    │    │
    # G────H────I
    cycles = find_simple_cycles(adjacency, max_ring_count)

    return cycles


def find_simple_cycles(adjacency, max_ring_count):
    """在无向图中寻找简单环，最多返回 max_ring_count 个（迭代 DFS，避免长环递归溢出）"""
    cycles = set()
    # 搜索预算在所有起点间共享，避免“每个起点各跑 MAX_SEARCH_COUNT 步”的最坏情况
    budget = MAX_SEARCH_COUNT

    for start in sorted(adjacency):
        visited = {start}
        path = [start]
        # 栈元素：(当前顶点, 该顶点的邻居迭代器)
        stack = [(start, iter(adjacency[start]))]

        while stack:
            current, neighbors = stack[-1]

            try:
                neighbor = next(neighbors)
            except StopIteration:
                stack.pop()
                path.pop()
                visited.discard(current)
                continue

            # 回到起点：形成一个环
            if neighbor == start:
                if len(path) >= 3:
                    cycles.add(canonical_cycle(path))
                    # 达到数量限制，立即终止整个搜索
                    if len(cycles) >= max_ring_count:
                        return [list(cycle) for cycle in cycles]
                continue

            # 跳过已访问
            if neighbor in visited:
                continue

            # 剪枝：start 为排序后更小一侧的顶点，只允许访问 >= start 的顶点
            if neighbor < start:
                continue

            # 超过搜索次数
            budget -= 1
            if budget < 0:
                return None

            visited.add(neighbor)
            path.append(neighbor)
            stack.append((neighbor, iter(adjacency[neighbor])))

    return [list(cycle) for cycle in cycles]


def canonical_cycle(cycle):
    """将无序环转换成唯一表示"""

    # 例
    # 1 ─ 2
    # │   │
    # 4 ─ 3
    n = len(cycle)

    # cycle：1 → 2 → 3 → 4
    # reversed(cycle)：1 → 4 → 3 → 2
    # 得到
    # [
    #     (1, 2, 3, 4),
    #     (2, 3, 4, 1),
    #     (3, 4, 1, 2),
    #     (4, 1, 2, 3),
    #
    #     (3, 2, 1, 4),
    #     (2, 1, 4, 3),
    #     (1, 4, 3, 2),
    # ]
    # 这些均为单个环的不同走法
    rotations = []
    for seq in (cycle, list(reversed(cycle))):
        for i in range(n):
            rotated = seq[i:] + seq[:i]

            rotations.append(
                tuple(rotated)
            )

    # 取字典序最小的那个作为标识
    return min(rotations)


def get_ring_lengths(obj, rings):
    """计算每个顶点环的边长总和，返回 {ring: length}
    只为环上的相邻顶点对计算距离，避免每次都为整张网格的所有边求长度。"""
    mesh = obj.data

    # 网格中真实存在的边（用来排除环上并不存在的顶点对）
    edge_keys = {get_edge_key(*edge.vertices) for edge in mesh.edges}

    ring_lengths = {}
    for ring in rings:
        total_length = 0.0
        count = len(ring)

        for i in range(count):
            v0 = ring[i]
            v1 = ring[(i + 1) % count]
            if get_edge_key(v0, v1) not in edge_keys:
                continue
            total_length += (mesh.vertices[v0].co - mesh.vertices[v1].co).length

        ring_lengths[tuple(ring)] = total_length

    return ring_lengths


def get_upper_ring(obj, z_target):
    """返回 z = z_target 处边长总和最大的顶点圈"""
    rings = get_rings_at_z(obj, z_target)
    if not rings:
        return set()

    ring_lengths = get_ring_lengths(obj, rings)
    sorted_rings = sorted(ring_lengths, key=ring_lengths.get, reverse=True)

    return set(sorted_rings[0])


def get_under_ring(obj, z_target, b_names):
    """返回 z = z_target 处，平均受胸部影响最小的环"""

    rings = get_rings_at_z(obj, z_target)

    if not rings:
        return set()

    # 按长度排序，避免结果随机
    ring_lengths = get_ring_lengths(obj, rings)
    sorted_rings = sorted(ring_lengths, key=ring_lengths.get, reverse=True)

    # 只有一个环
    if len(sorted_rings) == 1:
        return set(sorted_rings[0])

    max_ring = sorted_rings[0]
    max_length = ring_lengths[max_ring]

    second_length = ring_lengths[sorted_rings[1]]

    # 最大环明显大于其它环，直接使用
    if max_length >= second_length * 2:
        return set(max_ring)

    # 保留长度接近最大环的候选
    candidate_rings = [ring for ring in sorted_rings if ring_lengths[ring] * 2 >= max_length]

    # 寻找平均受胸部影响最小的环
    min_ring = min(
        candidate_rings,
        key=lambda ring: get_ring_bone_influence(obj, ring, b_names)
    )

    return set(min_ring)


def get_ring_bone_influence(obj, ring, b_names):
    """计算一个环平均胸部骨骼影响权重"""

    mesh = obj.data

    # 顶点组名称 -> index
    b_group_indices = {
        obj.vertex_groups[name].index
        for name in b_names
        if name in obj.vertex_groups
    }

    if not b_group_indices:
        return 0.0

    total_weight = 0.0

    for vid in ring:
        vert = mesh.vertices[vid]

        weight = sum(
            g.weight
            for g in vert.groups
            if g.group in b_group_indices
        )

        total_weight += weight

    return total_weight / len(ring)


def deselect_verts(obj):
    for v in obj.data.vertices:
        v.select = False
    for e in obj.data.edges:
        e.select = False
    for p in obj.data.polygons:
        p.select = False
    obj.data.update()


def get_ring_edge_length(obj, ring_verts):
    """计算指定顶点圈的总边长（直接读网格边，避免 bmesh 往返）"""
    mesh = obj.data
    ring = set(ring_verts)

    total = 0.0
    for edge in mesh.edges:
        v0, v1 = edge.vertices
        if v0 in ring and v1 in ring:
            total += (mesh.vertices[v0].co - mesh.vertices[v1].co).length
    return total


def island_min_distance(island_a, island_b, near_dist=None):
    """
    计算两个孤岛之间的最小顶点距离（KDTree 加速：为 island_b 建一次树，逐个查询最近点）
    （小于near_dist时提前退出）。

    KDTree = k-d tree（k 维空间索引树） 。
    Blender 的`mathutils.kdtree.KDTree` 是三维版本，用来回答“ 离这个点最近的点是哪个/有多远 ”这类问题。

    返回：
      float —— 两孤岛中最近一对顶点的距离
    """
    if not island_a or not island_b:
        return float("inf")

    # 预分配 KDTree：槽位数必须不小于实际插入的点数
    tree = KDTree(len(island_b))
    for index, vert in enumerate(island_b):
        # 插入坐标，附带一个负载（这里存顶点在 island_b 中的下标）
        tree.insert(vert.co, index)
    # 建树：所有 insert 完成后必须调用一次，否则查询无意义
    tree.balance()

    min_dist = float("inf")
    for vert in island_a:
        # 查询树中离 vert.co 最近的点，返回 (最近点坐标, 负载, 距离)
        _co, _index, dist = tree.find(vert.co)

        if dist < min_dist:
            min_dist = dist
            if near_dist is not None and min_dist < near_dist:
                return min_dist  # 已确认足够近，提前退出
            if min_dist == 0.0:
                return 0.0  # 已经是最小可能值，提前退出
    return min_dist


def keep_islands_between_z(tmp_obj, under_co, nipple_co, near_dist=1e-3):
    """
    保留 under_co、nipple_co 所在孤岛，删除其余孤岛。

    但是：如果某个其余孤岛与保留孤岛的最近顶点距离小于 near_dist，
    则认为它们在拓扑上"应当算作一体"（只是因为浮点误差或建模缝隙没连上），
    因此也保留这些孤岛，不删除。

    参数：
      tmp_obj   —— 临时网格对象
      under_co  —— 用于定位"下胸围"孤岛的参考坐标
      nipple_co —— 用于定位"乳首"孤岛的参考坐标
      near_dist —— 判定"足够接近、应保留"的距离阈值，默认 1e-3
    """
    with bmesh_from_mesh(tmp_obj.data) as bm:
        bm.verts.ensure_lookup_table()

        min_vert = min(bm.verts, key=lambda v: (v.co - under_co).length_squared)
        max_vert = min(bm.verts, key=lambda v: (v.co - nipple_co).length_squared)

        # 基础保留集合：under_co、nipple_co 所在的两个孤岛
        keep_verts = (
                set(get_mesh_island_verts(min_vert))
                | set(get_mesh_island_verts(max_vert))
        )

        # ------------------------------------------------------------------
        # 找出其余所有孤岛，逐个检查它们与 keep_verts 的最小距离。
        # 若距离小于 near_dist，说明这个孤岛"挨得很近"，
        # 很可能本应与保留孤岛相连，因此也加入 keep_verts，不删除。
        # ------------------------------------------------------------------
        other_islands = get_other_islands(bm, keep_verts)
        for island in other_islands:
            dist = island_min_distance(island, keep_verts, near_dist=near_dist)
            if dist < near_dist:
                keep_verts |= island

        delete_verts = [v for v in bm.verts if v not in keep_verts]

        if delete_verts:
            bmesh.ops.delete(
                bm,
                geom=delete_verts,
                context="VERTS",
            )

        bm.to_mesh(tmp_obj.data)
        tmp_obj.data.update()


def collect_island(start_vert, visited):
    """
    从 start_vert 出发，沿边遍历收集整个连通孤岛。

    参数：
      start_vert —— 起始顶点
      visited    —— 已访问顶点集合（会被原地修改：遍历到的顶点都会加入其中）
                     传入时可以预先包含"需要排除"的顶点，
                     这样遍历会自然跳过它们，不会扩散进已排除的区域

    返回：
      island —— 本次从 start_vert 出发能走到的所有顶点组成的 set
                （不包含调用前 visited 中已有的顶点）
    """
    island = set()
    stack = [start_vert]

    while stack:
        v = stack.pop()
        if v in visited:
            continue
        visited.add(v)
        island.add(v)

        for e in get_sorted_link_edges(v):
            ov = e.other_vert(v)
            if ov not in visited:
                stack.append(ov)

    return island


def get_mesh_island_verts(start_vert):
    """
    获取 start_vert 所在的整个连通孤岛（不考虑排除集合）。
    """
    return collect_island(start_vert, visited=set())


def island_center(island):
    center = Vector()
    for v in island:
        center += v.co
    return center / len(island)


def get_other_islands(bm, exclude_verts):
    """
    找出 bm 中除 exclude_verts 所在范围外的所有连通孤岛。

    参数：
      bm            —— bmesh 对象
      exclude_verts —— 需要排除的顶点集合（通常是已确定要保留的孤岛顶点）

    返回：
      list[set] —— 每个 set 是一个孤岛的顶点集合（不包含 exclude_verts 中的顶点）
    """
    visited = set(exclude_verts)
    islands = []

    for v in bm.verts:
        if v in visited:
            continue
        islands.append(collect_island(v, visited))

    # 排序，避免遍历孤岛的顺序对结果造成影响：
    # 1. 顶点数量降序
    # 2. 顶点数一致时，按质心 z, x, y 升序
    island_data = [
        (island, island_center(island))
        for island in islands
    ]

    island_data.sort(
        key=lambda item: (
            -len(item[0]),
            item[1].z,
            item[1].x,
            item[1].y
        )
    )

    islands = [item[0] for item in island_data]

    return islands


def crop_mesh_by_z(obj, z_min, z_max):
    """获取 z_min ~ z_max 之间的部分"""
    mesh = obj.data

    with bmesh_from_mesh(mesh) as bm:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]

        # 删除 z > z_max
        bmesh.ops.bisect_plane(
            bm,
            geom=geom,
            plane_co=(0, 0, z_max),
            plane_no=(0, 0, 1),
            clear_outer=True,
            clear_inner=False,
        )

        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]

        # 删除 z < z_min
        bmesh.ops.bisect_plane(
            bm,
            geom=geom,
            plane_co=(0, 0, z_min),
            plane_no=(0, 0, 1),
            clear_outer=False,
            clear_inner=True,
        )

        bm.to_mesh(mesh)
        mesh.update()


def _same_matrix(objs):
    """所有对象是否共享同一套世界矩阵（此时合并不会把顶点变换到别的坐标系）"""
    base = objs[0].matrix_world
    return all(
        all(abs(base[r][c] - obj.matrix_world[r][c]) <= 1e-6 for r in range(4) for c in range(4))
        for obj in objs[1:]
    )


def _z_bounds(obj):
    """网格数据自身的局部 z 范围（不含修改器/姿态，和复制出来的网格一致）"""
    mesh = obj.data
    count = len(mesh.vertices)
    if count == 0:
        return None
    co = array.array('f', [0.0]) * (count * 3)
    mesh.vertices.foreach_get("co", co)
    zs = co[2::3]
    return min(zs), max(zs)


def process_source_objs(data):
    """
    1. 复制对象并进行切割，减少后续处理耗时。
    2. 重映射胸部权重，提高胸部边缘区域权重，从而提升尺码选择时的准确率。
    """
    # 获取源数据
    source_objs = data["source_objs"]
    b_names_l = data["b_names_l"]
    b_names_r = data["b_names_r"]
    vg_names = b_names_l + b_names_r

    # 先算出胸部 z 区间，只复制可能落在这个区间内的对象：
    # 头、头发、眼、鞋等整块网格在切割阶段会被整块删掉，复制 → 合并 → 切割它们纯属浪费。
    # 合并时若各对象矩阵不一致，顶点会被变换到活动对象的空间，区间不可直接比较，此时退回原流程。
    filter_by_z = len(source_objs) > 1 and _same_matrix(source_objs)
    if filter_by_z:
        max_z, min_z = get_max_min_z(source_objs, vg_names)
        # 没有任何胸部顶点时，不做过筛，避免合并空集合出错
        if max_z == float("-inf") or min_z == float("inf"):
            filter_by_z = False

    if filter_by_z:
        z_lo = min_z - 0.01
        z_hi = max_z + 0.01
        copy_objs = []
        for obj in source_objs:
            bounds = _z_bounds(obj)
            # 空网格合并后也会被整块删除
            if bounds is None:
                continue
            obj_z_lo, obj_z_hi = bounds
            # 整个对象都在切割范围外：合并后也只会被整块删除，直接跳过
            if obj_z_hi < z_lo or obj_z_lo > z_hi:
                continue
            copy_objs.append(obj)
    else:
        copy_objs = source_objs

    # 极端情况下（所有网格都没有顶点）退回完整流程，避免后续合并空集合报错
    if not copy_objs:
        filter_by_z = False
        copy_objs = source_objs

    # 创建拷贝数据
    tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
    tmp_objs = []
    for obj in copy_objs:
        tmp_obj = copy_obj(obj, tmp_col)
        tmp_obj.parent = None
        tmp_objs.append(tmp_obj)

    # 提前合并网格，避免因合并时机不同导致结果不一致
    deselect_all_objects()
    if len(tmp_objs) > 1:
        for tmp_obj in tmp_objs:
            try:
                tmp_obj.select_set(True)
            except RuntimeError:  # 物体不在视图层中，无法选中
                pass
        bpy.context.view_layer.objects.active = tmp_objs[-1]
        bpy.ops.object.join()
    else:
        select_and_activate(tmp_objs[0])
    copied_source_obj = bpy.context.view_layer.objects.active
    copied_source_objs = [copied_source_obj]
    tmp_col.objects.unlink(copied_source_obj)

    # 获取胸部顶点的 max_z 和 min_z（已在复制前算过则直接复用）
    if not filter_by_z:
        max_z, min_z = get_max_min_z(copied_source_objs, vg_names)

    # 切割 copied_source_objs，保留 0.01m 的冗余范围
    for obj in copied_source_objs:
        crop_mesh_by_z(obj, min_z - 0.01, max_z + 0.01)

    # 移除顶点数为0的对象
    for obj in copied_source_objs[:]:
        if len(obj.data.vertices) == 0:
            bpy.data.objects.remove(obj, do_unlink=True)
            copied_source_objs.remove(obj)

    # 将胸部权重集中到胸.L和胸.R
    for obj in copied_source_objs:
        for name_l in b_names_l:
            trans_vg(obj, name_l, BREAST_BL_NAME_L)
        for name_r in b_names_r:
            trans_vg(obj, name_r, BREAST_BL_NAME_R)

    # 重映射胸部权重
    for obj in copied_source_objs:
        remap_breast_weights(obj)

    # 重设data参数
    b_names = [BREAST_BL_NAME_L, BREAST_BL_NAME_R]
    b_names_l = [BREAST_BL_NAME_L]
    b_names_r = [BREAST_BL_NAME_R]
    copied_obj_vgs_map = get_obj_vgs_map(copied_source_objs, b_names_l)
    data["b_names"] = b_names
    data["b_names_l"] = b_names_l
    data["b_names_r"] = b_names_r
    data["copied_source_objs"] = copied_source_objs
    data["copied_obj_vgs_map"] = copied_obj_vgs_map


def get_max_min_z(copied_source_objs, vg_names):
    """获取指定顶点组顶点的 max_z 和 min_z"""

    max_z = float("-inf")
    min_z = float("inf")

    for obj in copied_source_objs:
        mesh = obj.data
        vgs = obj.vertex_groups

        # 目标顶点组索引
        vg_indices = set()
        for name in vg_names:
            vg = vgs.get(name)
            if vg is not None:
                vg_indices.add(vg.index)

        if not vg_indices:
            continue

        for v in mesh.vertices:
            # 直接遍历权重，避免每个顶点生成一个 any() 生成器
            for g in v.groups:
                if g.group in vg_indices:
                    z = v.co.z
                    if z < min_z:
                        min_z = z
                    if z > max_z:
                        max_z = z
                    break

    return max_z, min_z


class AnalysisOperator(bpy.types.Operator):
    bl_idname = f"{ADDON_ID_PREFIX}.analysis"  # 引用时的唯一标识符
    bl_label = "执行"  # 显示名称（F3搜索界面，不过貌似需要注册，和panel中显示的内容区别开）
    bl_options = {'REGISTER', 'UNDO'}  # 启用撤销功能

    @classmethod
    def description(cls, context, properties):
        tips = [
            "执行后选中网格对象并进入编辑模式，可查看参考点的选取情况",
            "由于参考点的选取误差及尺码计算的简化，结果可能与实际存在偏差",
            "显示的结果为插件筛选后的值，切换系统控制台可查看完整执行结果",
            "「尺码列表」指逐步扩展胸部网格范围后，计算得到的尺码列表",
            "「尺码列表（扩展）」指在部分服装影响胸部形状，导致「尺码范围」不准确时，用于参考的扩展尺码列表"
        ]

        tip_index = {
            "DEFAULT": [0, 1, 2, 3, 4],
            "BASEBODY": [0, 2, 3],
        }

        analysis_type = context.scene.mmd_jiggle_tools_analysis.type

        show_tips = [
            f"{i + 1}：{tips[index]}"
            for i, index in enumerate(tip_index.get(analysis_type, []))
        ]

        return (
                "分析模型胸部尺码\n"
                "\n"
                "注意事项：\n"
                + "\n".join(show_tips)
        )

    def execute(self, context):
        """
        分析胸部尺寸。

        【通用模式】
        该模式下的分析结果为估算值，实际结果可能偏大或偏小，需结合多组阈值批量分析结果进行综合判断。
        胸部区域的定义为：胸部顶点组中，权重高于指定阈值的顶点所构成的网格。
        通常情况下，将胸部近似简化为半球模型，并以乳首与乳根之间的垂直距离（共2r）作为胸部尺寸的参考依据。
        但模型在存在束腰、束胸等形体压缩效果时，会对罩杯计算产生一定影响，但对于Joints（关节）上下限范围的计算无太多影响。

        【素体模式】
        该模式基于完整素体，通过上胸围与下胸围的差值计算胸部尺寸，结果较【通用模式】更加准确。
        但3D模型与真实人体结构仍存在一定差异，例如：上胸围测量时，背部曲线所占的长度可能（远）大于下胸围测量时的背部长度
        """

        scene = context.scene
        props = scene.mmd_jiggle_tools_analysis
        self.start_time = time.time()

        self.active_object = context.active_object
        self.selected_objects = bpy.context.selected_objects

        # 清理上一次执行时保留的临时对象（在进度条显示之前完成）
        clean_temp_data([TMP_COLLECTION_NAME, TMP_ANALYSIS_COLLECTION_NAME])
        # 记录分析开始前的数据块，后续只清理本次新增的，避免误删用户数据
        self.data_snapshot = snapshot_data_blocks()

        # 参数校验
        err_msg, self.data = validate_analysis_param(self.active_object, props.extra_bones_str)
        if err_msg:
            self.report({'ERROR'}, err_msg)
            return {'FINISHED'}

        self.initialized = False
        self.thresholds = ANALYSIS_THRESHOLDS
        self.analysis_index = 0
        self.further_index = 0
        self.results = []
        self.all_done = False
        # 进度条所在面板首次展开需要重新布局，先跳过两帧并主动重绘（见 modal）
        self.flush_count = 0
        # 进度更新后是否还要多留一轮 TIMER，让界面把它画出来
        self.hold_progress = False

        # 先显示“预处理中...”，让面板有时间把进度条画完整；
        # preprocess() 与后续合并网格放到 modal 里执行
        self.update_progress(props, force=True)
        # 防止页面变化
        restore_selection(self.selected_objects, self.active_object)

        # 后台（-b）没有事件循环，modal 不会被驱动，直接同步跑完所有分析步骤
        if bpy.app.background:
            return self.run_sync(context, props)

        self.timer = context.window_manager.event_timer_add(TIMER_INTERVAL, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def run_sync(self, context, props):
        """后台模式：同步跑完所有分析步骤，供 -b 测试使用"""
        try:
            while True:
                status = self._process(context)
                if 'FINISHED' in status:
                    return {'FINISHED'}
                if 'CANCELLED' in status:
                    return {'CANCELLED'}
        except Exception as e:
            self._end(context)
            try:
                restore_selection(self.selected_objects, self.active_object)
            except Exception:
                pass
            traceback.print_exc()
            self.report({'ERROR'}, f"分析过程发生异常：{e}")
            return {'CANCELLED'}

    def _end(self, context):
        """移除定时器并复位处理状态，保证任何结束路径都不会留下“处理中”状态"""
        if getattr(self, "timer", None) is not None:
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None

        set_batch_progress(TASK_ANALYSIS, False)
        tag_redraw(context)
        # 只清理本次新增的无用户数据块，避免影响用户已有数据
        purge_new_orphan_data(getattr(self, "data_snapshot", None))

    def modal(self, context, event):
        # 允许用户通过 ESC 结束分析，避免流程无法退出
        if event.type == 'ESC':
            self._end(context)
            self.report({'INFO'}, "已取消分析")
            return {'CANCELLED'}

        # 忽略用户的操作，仅处理TIMER事件，
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}

        # 进度条所在面板首次展开需要重新布局：先跳过两帧并主动重绘，避免界面来不及画
        if self.flush_count < 20:
            self.flush_count += 1
            tag_redraw(context)
            return {'RUNNING_MODAL'}

        # 每个进度多留一轮 TIMER 再继续：只留一轮时，两次进度更新有可能被合并进
        # 同一次界面重绘，看起来就像跳了一格（16.67% → 50%）
        if self.hold_progress:
            self.hold_progress = False
            tag_redraw(context)
            return {'RUNNING_MODAL'}

        try:
            status = self._process(context)
        except Exception as e:
            # 任何未预期异常都结束流程并复位状态，避免modal静默中断后UI卡死
            self._end(context)
            try:
                restore_selection(self.selected_objects, self.active_object)
            except Exception:
                pass
            self.report({'ERROR'}, f"分析过程发生异常：{e}")
            return {'CANCELLED'}

        if 'RUNNING_MODAL' not in status:
            return status

        tag_redraw(context)
        self.hold_progress = True
        return {'RUNNING_MODAL'}

    def _process(self, context):
        # 复制对象并进行切割，减少后续处理耗时
        # 重映射胸部权重，提高胸部边缘区域权重，从而提升尺码选择时的准确率
        if not self.initialized:
            # 预处理
            preprocess(self.active_object, self.data["source_objs"])
            process_source_objs(self.data)
            self.initialized = True
            self.face_cache = collect_face_records(self.data["copied_obj_vgs_map"])
            # 各档阈值共用同一张网格：顶点权重总和不随阈值变化，只扫描一次
            self.weight_cache = {}
            # 每次执行后，隐藏临时集合并恢复对象状态防止页面变化
            tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
            context.scene.collection.children.unlink(tmp_col)
            restore_selection(self.selected_objects, self.active_object)
            # 更新进度
            self.update_progress(context.scene.mmd_jiggle_tools_analysis)
            return {'RUNNING_MODAL'}

        scene = context.scene
        props = scene.mmd_jiggle_tools_analysis
        analysis_type = props.type
        remove_tmp = props.remove_tmp

        source_objs = self.data["source_objs"]
        copied_source_objs = self.data["copied_source_objs"]
        copied_obj_vgs_map = self.data["copied_obj_vgs_map"]
        b_names = self.data["b_names"]

        if not self.all_done:
            # 每次执行前，显示临时集合供后续操作
            tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
            if tmp_col.name not in scene.collection.children:
                scene.collection.children.link(tmp_col)

            if analysis_type == "DEFAULT":
                if self.analysis_index < len(self.thresholds):
                    threshold = self.thresholds[self.analysis_index]
                    result = do_analysis(copied_obj_vgs_map, threshold, self.analysis_index, analysis_type,
                                         face_cache=self.face_cache, weight_cache=self.weight_cache)
                    self.results.append(result)
                    self.analysis_index += 1
                    self.update_progress(props)
                else:
                    self.all_done = True
            else:
                if self.analysis_index < len(self.thresholds):
                    threshold = self.thresholds[self.analysis_index]
                    result = do_analysis(copied_obj_vgs_map, threshold, self.analysis_index, analysis_type,
                                         face_cache=self.face_cache, weight_cache=self.weight_cache)
                    self.results.append(result)
                    self.analysis_index += 1
                    self.update_progress(props)
                elif self.further_index < len(self.thresholds):
                    result = self.results[self.further_index]
                    # 六档阈值共用同一张源网格：清理只做一次，后续各档直接复制清理后的网格
                    if self.further_index == 0:
                        clean_base_body_source(copied_source_objs)
                    further_process_for_base_body(copied_source_objs, b_names, result, self.further_index,
                                                  clean_mesh=False)
                    self.further_index += 1
                    self.update_progress(props)
                else:
                    self.all_done = True

            # 每次执行后，隐藏临时集合并恢复对象状态防止页面变化
            scene.collection.children.unlink(tmp_col)
            restore_selection(self.selected_objects, self.active_object)
            return {'RUNNING_MODAL'}
        else:
            results = self.results

            # 若results全部异常，则直接拦截。
            if all(r.status != AnalysisStatus.OK for r in results):
                remove_tmp_obj(results, remove_tmp, analysis_type, copied_source_objs)
                restore_selection(self.selected_objects, self.active_object)
                self._end(context)
                msg = build_all_failed_msg(results)
                self.report({'ERROR'}, msg)
                return {'FINISHED'}

            # 从多次执行的结果中，筛选出最合适的那个结果
            result, cups, cup_inners = filter_results(results)
            nipple_co = result.nipple_co
            under_co = result.under_co

            # 选中源对象中的对应顶点
            select_verts(source_objs, [nipple_co, under_co])

            # 根据flag删除临时对象
            remove_tmp_obj(results, remove_tmp, analysis_type, copied_source_objs)

            # 设置Joint参数到插件面板
            set_joint_param(context, result)

            # 恢复对象的显示状态与选择状态
            restore_selection(self.selected_objects, self.active_object)
            self._end(context)

            # 构建显示消息
            total_time = time.time() - self.start_time
            msg = build_msg(results, result, analysis_type, cups, cup_inners, total_time)
            self.report({'INFO'}, "\n".join(msg))

            return {'FINISHED'}

    def update_progress(self, props, force=False):
        """更新进度"""
        # 预处理阶段还没有百分比，先显示文字
        if force:
            set_batch_progress(TASK_ANALYSIS, True, 0.0, ANALYSIS_PREPROCESS_TEXT)
            tag_redraw(bpy.context)
            return

        count = len(self.thresholds)

        if props.type == "DEFAULT":
            total = count
            current = self.analysis_index + 1
        else:
            total = count * 2
            current = self.analysis_index + 1

            if self.analysis_index == count:
                current = count + self.further_index + 1

        factor = min(current / total, 1.0)
        if not self.all_done and factor == 1:
            factor = 0.9999

        set_batch_progress(TASK_ANALYSIS, True, factor, f"进度 {factor:.2%}", current, total)
        tag_redraw(bpy.context)


def build_all_failed_msg(results):
    """整理全部失败时的错误信息"""

    seen = set()
    unique_results = []

    for r in results:
        if r.status in seen:
            continue
        seen.add(r.status)
        unique_results.append(r)

    # 只有一种错误类型
    if len(unique_results) == 1:
        msg = unique_results[0].msg
    else:
        msg = "\n".join(f"[权重阈值={r.threshold}] {r.msg}" for r in results)
    return msg


def set_joint_param(context, result):
    """设置Joint参数到插件面板"""

    settings = context.scene.mmd_jiggle_tools_set_rgba

    # 计算胸部尺寸
    breast_x = result.breast_x
    breast_y = result.breast_y
    breast_z = result.breast_z

    mode = settings.jiggle_adjustment_mode

    if mode == 'CUSTOM_SIMPLIFY':
        settings.breast_x = breast_x
        settings.breast_y = breast_y
        settings.breast_z = breast_z

    elif mode == 'CUSTOM':
        settings.limit_lin_x_lower = -breast_x * 2
        settings.limit_lin_x_upper = breast_x * 2

        settings.limit_lin_y_lower = -breast_y * 2
        settings.limit_lin_y_upper = breast_y * 2

        settings.limit_lin_z_lower = -breast_z * 0.5
        settings.limit_lin_z_upper = breast_z * 0.5

        adjust_ang_limits(settings, result.cup_index)

        if settings.sensitivity != 1:
            settings.limit_ang_x_sync = False
            settings.limit_ang_x_lower = math.radians(0)


def build_msg(results, result, analysis_type, cups, cup_inners, total_time):
    msg = []
    msg.append(f"尺码: {result.cup}")
    if analysis_type == "DEFAULT":
        breast_x = result.breast_x
        breast_y = result.breast_y
        breast_z = result.breast_z

        msg.append(f"单侧胸宽: {format_cm(breast_x)}")
        msg.append(f"胸部深度: {format_cm(breast_y)}")
        msg.append(f"胸部高度: {format_cm(breast_z)}")
        msg.append(f"尺码列表: {cups}")
        msg.append(f"尺码列表（扩展）: {cup_inners if cup_inners else []}")

        total_msg = build_log_table(results,
                                    ("Status", "Threshold", "Side Width", "Depth", "Height", "Size", "Size (Extended)"),
                                    fill_row)
    else:
        upper_bust = result.upper_bust
        under_bust = result.under_bust
        bust_diff = result.bust_diff
        msg.append(f"上胸围: {format_cm(upper_bust)}")
        msg.append(f"下胸围: {format_cm(under_bust)}")
        msg.append(f"围差: {format_cm(bust_diff)}")

        total_msg = build_log_table(results,
                                    ("Status", "Threshold", "Upper Bust", "Under Bust", "Bust Diff", "Size"),
                                    fill_bust_row)
    print(total_msg)
    print(f"耗时 {total_time:.2f}s。")

    return msg


def remove_tmp_obj(results, remove_tmp, analysis_type, copied_source_objs):
    if remove_tmp:
        for r in results:
            tmp_obj = r.tmp_obj
            tmp_obj_for_base_body = r.tmp_obj_for_base_body
            if tmp_obj:
                bpy.data.objects.remove(tmp_obj, do_unlink=True)
            if analysis_type != "DEFAULT" and tmp_obj_for_base_body:
                bpy.data.objects.remove(tmp_obj_for_base_body, do_unlink=True)
        for name in (TMP_COLLECTION_NAME, TMP_ANALYSIS_COLLECTION_NAME):
            collection = bpy.data.collections.get(name)
            if collection is not None:
                bpy.data.collections.remove(collection)
    else:
        if analysis_type != "DEFAULT":
            for r in results:
                if r.tmp_obj:
                    bpy.data.objects.remove(r.tmp_obj, do_unlink=True)
            collection = bpy.data.collections.get(TMP_COLLECTION_NAME)
            if collection is not None:
                bpy.data.collections.remove(collection)

        # 恢复临时集合显示（仅当集合存在时，避免创建空集合）
        tmp_col = bpy.data.collections.get(TMP_ANALYSIS_COLLECTION_NAME)
        if tmp_col is not None and tmp_col.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(tmp_col)

    for obj in copied_source_objs:
        bpy.data.objects.remove(obj, do_unlink=True)


def fill_row(r):
    return (
        r.status.name,
        r.threshold,
        format_cm(r.breast_x, unit=False),
        format_cm(r.breast_y, unit=False),
        format_cm(r.breast_z, unit=False),
        r.cup if r.cup else 'None',
        r.cup_inner if r.cup_inner else 'None',
    )


def fill_bust_row(r):
    return (
        r.status.name,
        r.threshold,
        format_cm(r.upper_bust, unit=False),
        format_cm(r.under_bust, unit=False),
        format_cm(r.bust_diff, unit=False),
        r.cup if r.cup else 'None',
    )


def build_log_table(results, header, row_fn):
    def safe(v):
        return "None" if v is None else str(v)

    rows = [header]
    for r in results:
        rows.append(tuple(safe(v) for v in row_fn(r)))

    cols = len(header)
    widths = [max(len(str(row[i])) for row in rows) for i in range(cols)]

    def make_line(row):
        return "│ " + " │ ".join(
            str(row[i]).ljust(widths[i]) for i in range(cols)
        ) + " │"

    def make_sep(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right

    top = make_sep("┌", "┬", "┐")
    mid = make_sep("├", "┼", "┤")
    bottom = make_sep("└", "┴", "┘")

    lines = [top]
    for i, row in enumerate(rows):
        lines.append(make_line(row))
        if i == 0:
            lines.append(mid)
    lines.append(bottom)

    return "\n".join(lines)


def get_obj_vgs_map(objs, b_names_l):
    """校验网格对象中是否存在指定名称的顶点组，并返回 map"""

    target_names = set(b_names_l)
    obj_vgs_map = {}

    for obj in objs:
        vg_names = {vg.name for vg in obj.vertex_groups}

        hit_groups = list(target_names & vg_names)

        if hit_groups:
            obj_vgs_map[obj] = hit_groups

    return obj_vgs_map


def validate_analysis_param(active_object, extra_bones):
    """参数校验"""
    if active_object is None:
        return "请选择模型！", None

    # 根节点
    # 获取根对象下所有的骨架和网格。扩大适用范围，不局限于MMD模型及其结构
    root = find_ancestor(active_object)
    if not root:
        return "未找到模型根节点！", None

    # 骨架
    source_armatures = find_objs(root, obj_type="ARMATURE")
    if not source_armatures:
        return "源模型中未找到骨架！", None

    # 网格
    mesh_objs = find_objs(root, obj_type="MESH")
    if not mesh_objs:
        return "源模型中未找到网格！", None
    # 只处理视图里可见的网格，隐藏的对象不纳入分析范围
    source_objs = [obj for obj in mesh_objs if is_visible_in_viewport(obj)]
    if not source_objs:
        return "请确保网格在3D视图中可见！", None

    # 胸部骨骼
    breast_bones_map = get_breast_bones_map(source_armatures, extra_bones)
    if not breast_bones_map:
        return "源模型中未找到胸部骨骼！", None

    # 左右骨骼拆分
    b_names = []
    b_names_l = []
    b_names_r = []

    for _, bones in breast_bones_map.items():
        for bone in bones:
            b_names.append(bone.name)
            if bone.tail_local.x > 0:
                b_names_l.append(bone.name)
            else:
                b_names_r.append(bone.name)

    if not b_names_l:
        return "源模型中未找到左侧胸部骨骼！", None

    # 网格对象 -> 网格对象所含胸部顶点组
    obj_vgs_map = get_obj_vgs_map(source_objs, b_names_l)
    if not obj_vgs_map:
        return "未找到与胸部骨骼对应的顶点组！", None

    return None, {
        "root": root,
        "source_armatures": source_armatures,
        "source_objs": source_objs,
        "breast_bones_map": breast_bones_map,
        "b_names": b_names,
        "b_names_l": b_names_l,
        "b_names_r": b_names_r,
        "obj_vgs_map": obj_vgs_map,
    }


def preprocess(active_object, source_objs):
    """预处理：准备选择状态与点选择模式，不改动物体可见性"""
    deselect_all_objects()
    for obj in source_objs:
        select_and_activate(obj)

    # 切换为“点”选择模式并清空选择：后续 select_verts 只标出乳首/乳根两个点，
    # 先清空可避免用户原有的顶点选择混进来。
    # 直接写网格数据与工具设置，避免让全部网格进出编辑模式
    for obj in source_objs:
        mesh = obj.data
        for elem in (mesh.vertices, mesh.edges, mesh.polygons):
            count = len(elem)
            if count:
                elem.foreach_set("select", [False] * count)
        mesh.update()

    # https://docs.blender.org/api/current/bpy.types.ToolSettings.html#bpy.types.ToolSettings.mesh_select_mode
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)

    # 恢复选择
    deselect_all_objects()
    select_and_activate(active_object)


def get_verts_influenced_by_bones(obj, bone_names, threshold, right=True, weight_cache=None):
    """
    获取受指定骨骼影响且总权重大于 threshold 的顶点

    weight_cache：同一次分析内多档阈值共用同一张网格，各顶点的权重总和不随阈值
    变化，传入字典可只扫描一遍顶点（键：对象指针 + 骨骼名 + 左右侧）。
    """
    vgs = obj.vertex_groups

    # 骨骼名 转为 对应顶点组索引
    target_groups = set()
    for name in bone_names:
        vg = vgs.get(name)
        if vg is not None:
            target_groups.add(vg.index)

    key = None
    if weight_cache is not None:
        key = (obj.as_pointer(), tuple(bone_names), right)
        cached = weight_cache.get(key)
        if cached is not None:
            refs, totals, max_weight = cached
            return [v for v, w in zip(refs, totals) if w > threshold], max_weight

    refs = []
    totals = []
    max_weight = 0.0
    for v in obj.data.vertices:
        # 仅根据 bone_names 判断左右侧可能存在误差（如权重绑定错误：少女前线2的克莱妲），因此需通过顶点实际位置进行二次校验。
        if right and v.co.x < 0:
            continue

        total_weight = 0.0

        for g in v.groups:
            # 用顶点组索引set比对，避免使用 obj.vertex_groups[g.group].name，太耗时了
            if g.group in target_groups:
                total_weight += g.weight

        if total_weight > max_weight:
            max_weight = total_weight

        refs.append(v)
        totals.append(total_weight)

    if key is not None:
        weight_cache[key] = (refs, totals, max_weight)

    return [v for v, w in zip(refs, totals) if w > threshold], max_weight


def delete_unwanted_vertices(obj, keep_vertex_indices):
    """
    在 Object 模式下删除不需要的顶点（只保留 keep_vertex_indices）
    """

    mesh = obj.data

    with bmesh_from_mesh(mesh) as bm:
        bm.verts.ensure_lookup_table()

        keep_set = set(keep_vertex_indices)

        # 选择要删除的顶点
        delete_verts = [v for v in bm.verts if v.index not in keep_set]

        # 删除顶点
        bmesh.ops.delete(bm, geom=delete_verts, context='VERTS')

        # 写回 mesh
        bm.to_mesh(mesh)

    mesh.update()


def get_islands(bm):
    visited = set()
    islands = []

    for face in bm.faces:
        if face in visited:
            continue

        stack = [face]
        island = []

        while stack:

            f = stack.pop()

            if f in visited:
                continue

            visited.add(f)
            island.append(f)

            for e in f.edges:
                for lf in e.link_faces:
                    if lf not in visited:
                        stack.append(lf)

        islands.append(island)

    return islands


def island_area(island):
    return sum(f.calc_area() for f in island)


def get_best_island(bm):
    """
    获取网格的各个松散块，取松散块中面的面积最大且网格顶点数最多的那个松散块作为胸部范围（优先面积）（防止胸饰干扰）
    由于部分模型存在特殊结构，仍可能出现低概率误识别情况。对于此类异常案例（如《少女前线2：追放》中的“甜心奇迹”），当前方案保留人工干预机制，由用户进行最终修正。
    """
    islands = get_islands(bm)
    # 没有松散块
    if not islands:
        return AnalysisStatus.NO_ISLANDS, None, None, None

    # 最佳松散块
    best_island = None
    # 最佳松散块面积
    best_island_area = -1
    # 最佳松散块顶点数
    best_island_v_count = -1
    other_islands = []

    # 获取参考顶点所在松散块
    for island in islands:
        other_islands.append(island)
        verts = set()
        for f in island:
            verts.update(f.verts)
        area = island_area(island)
        v_count = len(verts)
        if area > best_island_area or (abs(area - best_island_area) < 1e-8 and v_count > best_island_v_count):
            best_island = island
            best_island_area = area
            best_island_v_count = v_count

    other_islands.remove(best_island)
    other_island_verts = set()
    for island in other_islands:
        for f in island:
            other_island_verts.update(f.verts)

    # 获取乳根所在松散块的边界顶点
    breast_faces = best_island
    breast_verts = set()
    for f in breast_faces:
        breast_verts.update(f.verts)

    boundary = set()
    breast_set = set(breast_verts)

    for e in bm.edges:
        if not e.is_boundary:
            continue
        if e.verts[0] in breast_set and e.verts[1] in breast_set:
            boundary.add(e.verts[0])
            boundary.add(e.verts[1])

    # 无边界
    if not boundary:
        return AnalysisStatus.NO_BOUNDARY, None, None, None

    return None, boundary, breast_verts, other_island_verts


def build_uv_island_cache(bm, temp_obj):
    """
    构建:
        vert -> island_id
        island_id -> verts
    """

    uv_islands = bpy_extras.mesh_utils.mesh_linked_uv_islands(temp_obj.data)

    vert_island_id = {}
    island_verts_map = {}

    for island_id, island in enumerate(uv_islands):

        island_verts = set()

        for fi in island:
            island_verts.update(bm.faces[fi].verts)

        island_verts_map[island_id] = island_verts

        for v in island_verts:
            vert_island_id[v] = island_id

    return vert_island_id, island_verts_map


def check_vert(v, vert_island_id, under_island_id):
    # 顶点不能位于temp_obj的边界边上，但前侧顶点和下侧顶点位于同一个uv则可忽略这项校验（防止短款夹克的干扰，恩雅_澜风_恋恋秋行）
    if any(e.is_boundary for e in v.link_edges):
        if vert_island_id.get(v) != under_island_id:
            return False

    # 顶点相邻的面，不能为服装背面（雷电将军）
    if v.link_faces and all(f.normal.y > 0 for f in v.link_faces):
        return False

    # 顶点应至少和三条边相连（茉莉安_幽潮_绒尾迷梦、雷电将军）
    if len(v.link_edges) < 3:
        return False

    for edge in get_sorted_link_edges(v):
        faces = edge.link_faces
        if len(faces) == 2:
            # 法线夹角
            angle = faces[0].normal.angle(faces[1].normal, 0)
            # 实际折角
            dihedral = math.pi - angle
            # 顶点相邻的面，相互间的夹角不能小于 LINKED_FACES_THRESHOLD 度
            if dihedral < LINKED_FACES_THRESHOLD:
                return False
    return True


def get_breast_nipple(bm, temp_obj, breast_verts, under_vert, other_island_verts, analysis_type):
    """
    获取胸部前侧参考顶点并修正。

    顶点相邻的面，相互间的夹角不能小于 LINKED_FACES_THRESHOLD 度（防止胸饰的干扰）
    顶点相邻的面，不能为服装背面（防止服装干扰，雷电将军）
    顶点应至少和三条边相连（茉莉安_幽潮_绒尾迷梦、雷电将军）
    顶点不能位于temp_obj的边界边上，但前侧顶点和下侧顶点位于同一个uv则可忽略这项校验（防止短款夹克的干扰，恩雅_澜风_恋恋秋行）

    如果出现任意一种情况，需要将顶点删除，将剩下的顶点重新设置为 breast_verts 再筛选
    """

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    breast_verts_set = set(breast_verts)

    vert_island_id, island_verts_map = build_uv_island_cache(bm, temp_obj)
    under_island_id = vert_island_id.get(under_vert)

    def find_upper_vert():
        if analysis_type == "DEFAULT":
            return min(sort_verts(breast_verts_set), key=lambda v: v.co.y)
        else:
            # 将顶点投影到直线 y = -x 上，取投影后 y 值最小的顶点 proj_vert，
            # 将 min_y_vert 与 proj_vert 的连接边的平均边长相比较，取较小的那个（乳首处通常较为密集）
            # 以防止因胸部较小导致肋骨凸起被误判为胸部顶点（Hina_BikiniB）
            # 即使这样处理，乳首也可能并不处于y值较小处（如 Fubuki），但对处理结果无太大影响。

            # --- 投影公式推导 ---
            # 直线 y = -x 过原点，方向向量取 d = (1, -1)
            # 点 p = (x, y) 投影到方向为 d 的直线上，公式为：
            #     proj = (p·d / |d|^2) * d
            # 其中 p·d = x*1 + y*(-1) = x - y，|d|^2 = 1^2 + (-1)^2 = 2
            # 代入得：
            #     proj = ((x - y) / 2) * (1, -1)
            #          = ( (x - y) / 2,  -(x - y) / 2 )
            # 即：
            #     proj_x = (x - y) / 2
            #     proj_y = (y - x) / 2
            # 验证：proj_y = -proj_x，满足直线方程 y = -x ✓
            # --------------------

            # 原始 y 坐标最小的顶点
            min_y_vert = min(sort_verts(breast_verts_set), key=lambda v: v.co.y)

            # 投影到 y = -x 上 y 值最小的顶点
            # 因为 proj_y = (y - x) / 2，要找投影 y 最小的点，
            # 等价于找 (y - x) 最小的点（除以 2 是正数缩放，不影响最小值结果）
            proj_vert = min(sort_verts(breast_verts_set), key=lambda v: v.co.y - v.co.x)

            # 比较两个候选顶点 link_edges 的平均长度，取边长较小的那个
            candidates = [min_y_vert, proj_vert]

            # 模型导入时，默认已清理无效顶点。
            # 但后续处理过程中，顶点删除可能会产生无边连接的孤立顶点。
            # 如果两个候选顶点均为孤立顶点，也无需处理，后续 check_vert 校验不会通过。（例如：卡拉彼丘 忧雾_三途雾徊_进阶无裙黄）
            if len(min_y_vert.link_edges) == 0:
                return proj_vert
            elif len(proj_vert.link_edges) == 0:
                return min_y_vert
            else:
                return min(
                    candidates,
                    key=lambda v: sum(e.calc_length() for e in get_sorted_link_edges(v)) / len(v.link_edges)
                )

    while True:
        if not breast_verts_set:
            return AnalysisStatus.NO_BREAST_VERTS_AFTER_DELETE_INVALID, None, None

        upper_vert = find_upper_vert()

        if check_vert(upper_vert, vert_island_id, under_island_id):
            break

        # 乳首的选取存在干扰，删除所在顶点并重新筛选
        breast_verts_set -= {upper_vert}
        other_island_verts -= {upper_vert}
        bmesh.ops.delete(bm, geom=[upper_vert], context='VERTS')
        bm.to_mesh(temp_obj.data)
        temp_obj.data.update()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

    return None, upper_vert, breast_verts_set


def get_breast_cleavage(max_x, breast_verts, other_island_verts):
    """
    获取胸部内侧参考顶点
    获取x在0~abs(max_x)/4范围内的的顶点列表并按照x大小排序
    默认取最左侧顶点作为内侧顶点，这种情况下x的增长对应胸型凸起，适用于素体等着衣较少的情况的乳沟的选取。
    如果穿了服装，则取从左到右时的局部峰值的最大值作为内侧顶点
    """

    verts = set()
    verts.update(other_island_verts)
    verts.update(breast_verts)

    # 获取x在 0~max_x/10 范围内的顶点，即靠近中线的区域
    denominator = 10
    inner_verts = [v for v in verts if 0 <= abs(v.co.x) <= max_x / denominator]

    if not inner_verts:
        return AnalysisStatus.NO_BREAST_INNER, None

    # 按 x 从小到大排序（从中线向外）
    inner_verts.sort(key=lambda v: v.co.x)

    # 默认取最左侧顶点（x最小，最靠近中线）
    best_cleavage_vert = inner_verts[0]

    # 获取局部峰值的最大值（仅局部峰值无法应对服装网格结构较为复杂的情况，如雷电将军）
    for i in range(1, len(inner_verts) - 1):
        prev_y = inner_verts[i - 1].co.y
        curr_y = inner_verts[i].co.y
        next_y = inner_verts[i + 1].co.y

        if curr_y >= prev_y and curr_y >= next_y:
            cleavage_vert = inner_verts[i]
            if best_cleavage_vert.co.y < cleavage_vert.co.y:
                best_cleavage_vert = cleavage_vert

    return None, best_cleavage_vert

def _chest_candidate_mask(obj, vg_names):
    """候选顶点掩码：x >= 0 且受指定顶点组影响（权重 > 0）。

    坐标约定（容易混淆，先说明）：
      - x >= 0 在视线上属于"右侧"，但对应模型自身的**左胸**（胸.L）
      - x < 0 对应模型自身的右胸（胸.R）

    与 get_verts_influenced_by_bones（right=True 只保留 x >= 0）的筛选口径一致：
    不满足条件的顶点不会出现在任何一档阈值的胸部顶点集合里，含这类顶点的面
    也永远不会命中反向面检测。
    """
    vgs = obj.vertex_groups
    target = set()
    for name in vg_names:
        vg = vgs.get(name)
        if vg is not None:
            target.add(vg.index)

    mesh = obj.data
    n = len(mesh.vertices)
    mask = bytearray(n)
    if not target or not n:
        return mask

    co = array.array('f', [0.0]) * (n * 3)
    mesh.vertices.foreach_get("co", co)

    for i, v in enumerate(mesh.vertices):
        # i * 3：计算当前顶点的 X 坐标在 co 数组中的索引。因为每个顶点占用 3 个数值，分别表示 X、Y、Z。
        if co[i * 3] < 0:
            continue  # x < 0 属于视线左侧 / 模型右胸（胸.R），上游不参与分析
        for g in v.groups:
            if g.group in target and g.weight > 0:
                mask[i] = 1
                break
    return mask


def collect_face_records(obj_vgs_map):
    """一次性收集网格面信息，供多档阈值复用（分析期间源网格不会被修改）。

    只收集"全部顶点都在 x >= 0 一侧且受胸部骨骼影响"的面：
    反向重合面检测会把顶点集合限定在每档阈值的胸部顶点内，
    其余面永远不会命中，提前跳过可以省掉大部分坐标键计算与 bmesh 往返。

    返回 {obj: [(面顶点索引集合, 坐标键, 法向键), ...]}
    """
    records = {}
    for obj, vg_names in obj_vgs_map.items():
        obj_records = []
        mesh = obj.data
        n_verts = len(mesh.vertices)
        n_faces = len(mesh.polygons)

        # 乳首、乳根、胸部宽度等参数单侧即可取到
        mask = _chest_candidate_mask(obj, vg_names)
        if n_verts and n_faces and any(mask):
            co = array.array('f', [0.0]) * (n_verts * 3)
            mesh.vertices.foreach_get("co", co)

            loop_verts = array.array('i', [0]) * len(mesh.loops)
            mesh.loops.foreach_get("vertex_index", loop_verts)

            starts = array.array('i', [0]) * n_faces
            mesh.polygons.foreach_get("loop_start", starts)
            totals = array.array('i', [0]) * n_faces
            mesh.polygons.foreach_get("loop_total", totals)

            normals = array.array('f', [0.0]) * (n_faces * 3)
            mesh.polygons.foreach_get("normal", normals)

            for f in range(n_faces):
                start = starts[f]
                end = start + totals[f]
                # 只要有一个顶点不在候选集合里，该面就不可能命中
                for k in range(start, end):
                    if not mask[loop_verts[k]]:
                        break
                # 执行了 break：跳过 else。
                # 循环正常结束，没有执行 break：执行 else。
                # 循环一次都没有执行：也会执行 else。
                else:
                    face_verts = [loop_verts[k] for k in range(start, end)]
                    face_verts.sort(key=lambda i: (co[i * 3 + 2], co[i * 3], co[i * 3 + 1]))
                    face_key = tuple(
                        get_co_key((co[i * 3], co[i * 3 + 1], co[i * 3 + 2])) for i in face_verts
                    )
                    normal_key = get_co_key((normals[f * 3], normals[f * 3 + 1], normals[f * 3 + 2]))
                    obj_records.append((frozenset(face_verts), face_key, normal_key))

        records[obj] = obj_records
    return records


def has_overlapping_faces_with_opposite_normals(obj_verts_map, face_cache=None):
    """检查网格物体是否存在重合且法向相反的面

    face_cache 可由 collect_face_records 预先生成：多档阈值共用同一份面数据，
    避免每档阈值都做一次 bmesh 转换和坐标键计算。
    """
    if face_cache is None:
        face_cache = collect_face_records(obj_verts_map)

    source_faces = {}

    for obj, verts in obj_verts_map.items():
        vert_indices = {v.index for v in verts}

        for face_verts, face_key, normal_key in face_cache.get(obj, ()):
            # 仅处理所有顶点都在指定 verts 中的面
            if not face_verts <= vert_indices:
                continue

            if face_key in source_faces:
                old_normal_key = source_faces[face_key]

                # 法向相反
                if old_normal_key == tuple(-v for v in normal_key):
                    return True
            else:
                source_faces[face_key] = normal_key
    return False


def do_analysis(obj_vgs_map, threshold, index, analysis_type, face_cache=None, weight_cache=None):
    """分析胸部尺寸"""
    # 网格对象 -> 胸部范围（高于权重阈值的顶点）
    obj_verts_map = {}
    max_weight = 0.0
    for source_obj, vg_names in obj_vgs_map.items():
        source_vertices, part_weight = get_verts_influenced_by_bones(source_obj, vg_names, threshold,
                                                                    weight_cache=weight_cache)
        if not source_vertices:
            continue
        obj_verts_map[source_obj] = source_vertices
        if part_weight > max_weight:
            max_weight = part_weight

    vg_names_l = sorted({vg for vgs in obj_vgs_map.values() for vg in vgs})
    if not obj_verts_map:
        return AnalysisResult(
            status=AnalysisStatus.NO_BREAST_VERTS,
            msg=f"{AnalysisStatus.NO_BREAST_VERTS.msg}顶点组名称参考：{vg_names_l}",
            threshold=threshold
        )

    # 检查网格物体是否存在重合且法向相反的面（例如：星穹铁道的真珠）
    # 无论放在通用阶段还是素体阶段，都会影响部分正常模型的检测结果。
    # 如果直接放在通用阶段，会引入不必要的检测；如果直接放在素体阶段，则会降低整体检测通过率。
    # 因此，这里仅在通用阶段记录 tag，在素体阶段进行拦截。
    # 这样可以检测出胸部区域存在的反向面问题，至于胸侧和后背区域，由于出现此类问题的概率较低，暂不进行处理。
    opposite_normals = False
    if has_overlapping_faces_with_opposite_normals(obj_verts_map, face_cache=face_cache):
        opposite_normals = True

    # 创建临时胸部对象
    tmp_col, tmp_obj, temp_mesh = create_tmp_obj(obj_verts_map, tmp_obj_name=DEFAULT_TMP_OBJ_NAME)

    with bmesh_from_mesh(temp_mesh) as bm:
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        # 获取参考顶点所在松散块
        status, boundary, breast_verts, other_island_verts = get_best_island(bm)
        if status in (AnalysisStatus.NO_ISLANDS, AnalysisStatus.NO_BOUNDARY):
            tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_None"
            return AnalysisResult(status=status, msg=status.msg, threshold=threshold, tmp_obj=tmp_obj)

        # 获取胸部宽度
        positive_x = [v.co.x for v in breast_verts if v.co.x >= 0]
        if not positive_x:
            tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_None"
            return AnalysisResult(status=AnalysisStatus.NO_BREAST_WIDTH,
                                  msg=AnalysisStatus.NO_BREAST_WIDTH.msg,
                                  threshold=threshold, tmp_obj=tmp_obj)
        max_x = max(positive_x)

        # 获取胸部下侧参考顶点（松散块中，z最小且位于边界边上、且位于胸部内侧的点）。
        lower = get_breast_lower(max_x, boundary)
        if lower is None:
            tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_None"
            return AnalysisResult(status=AnalysisStatus.NO_BREAST_LOWER,
                                  msg=AnalysisStatus.NO_BREAST_LOWER.msg,
                                  threshold=threshold, tmp_obj=tmp_obj)
        lower_co = lower.co

        # 获取胸部前侧参考顶点并修正
        status, nipple, breast_verts = get_breast_nipple(bm, tmp_obj, breast_verts, lower, other_island_verts,
                                                         analysis_type)
        if status == AnalysisStatus.NO_BREAST_VERTS_AFTER_DELETE_INVALID:
            tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_None"
            return AnalysisResult(status=status, msg=status.msg, threshold=threshold, tmp_obj=tmp_obj)
        nipple_co = nipple.co

        # 获取胸部内侧顶点，没有也无所谓
        inner_co = None
        status, inner_vert = get_breast_cleavage(max_x, breast_verts, other_island_verts)
        if inner_vert:
            inner_co = inner_vert.co

    lower_nipple_diff_y = (lower_co.y - nipple_co.y) * 2 * 100
    cup, cup_index = next((c, i) for i, (limit, c) in enumerate(CUP_LIST) if lower_nipple_diff_y < limit)

    if cup == CUP_LIST[-1][1]:
        tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_None"
        return AnalysisResult(status=AnalysisStatus.BUST_DIFF_TOO_LARGE,
                              msg=AnalysisStatus.BUST_DIFF_TOO_LARGE.msg,
                              threshold=threshold, tmp_obj=tmp_obj)

    cup_inner = None
    cup_inner_index = None
    if inner_vert:
        inner_nipple_diff_y = (inner_co.y - nipple_co.y) * 2 * 100
        cup_inner, cup_inner_index = next(
            (c, i) for i, (limit, c) in enumerate(CUP_LIST) if inner_nipple_diff_y < limit)

    # 内侧点判别结果不够稳定，因此仅在临时对象中显示
    cos = [nipple_co.copy(), lower_co.copy()]
    if inner_co:
        cos.append(inner_co.copy())
    select_verts([tmp_obj], cos)

    tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_{cup}"

    breast_x = max_x
    breast_y = lower_co.y - nipple_co.y
    breast_z = abs(nipple_co.z - lower_co.z) * 2

    return AnalysisResult(
        status=AnalysisStatus.OK,
        threshold=threshold,
        nipple_co=nipple_co.copy(),
        under_co=lower_co.copy(),
        inner_co=inner_co.copy() if inner_co else None,
        max_x=max_x,
        breast_x=breast_x,
        breast_y=breast_y,
        breast_z=breast_z,
        cup=cup,
        cup_index=cup_index,
        cup_inner=cup_inner,
        cup_inner_index=cup_inner_index,
        tmp_obj=tmp_obj,
        opposite_normals=opposite_normals,
        max_weight=max_weight
    )


def get_breast_lower(max_x, boundary):
    """获取胸部下侧顶点，找不到时返回 None"""

    center_x = max_x / 2

    lower_verts = []
    for denominator in range(10, 1, -1):
        ratio = 1 / denominator
        lower_verts = [v for v in boundary if center_x - max_x * ratio <= abs(v.co.x) <= center_x + max_x * ratio]
        if lower_verts:
            break

    if not lower_verts:
        return None

    lower_vert = min(
        sort_verts(lower_verts),
        key=lambda v: v.co.z
    )
    return lower_vert


def _clean_temp_mesh(mesh):
    """临时网格的固定清理：合并过近顶点 + 删除共享顶点的重合面"""
    with bmesh_from_mesh(mesh) as bm:
        bm.verts.ensure_lookup_table()

        # 按距离合并顶点，防止松散块过多，提高胸部乳首、乳根定位的准确性
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
        # 删除共享顶点的重合面，例如：3个顶点却构成了2个面，防止边界边的获取出现异常（符玄）
        seen_faces = {}
        faces_to_delete = []
        for f in bm.faces:
            # 用顶点索引的frozenset作为面的唯一标识
            key = frozenset(v.index for v in f.verts)
            if key in seen_faces:
                faces_to_delete.append(f)
            else:
                seen_faces[key] = f

        if faces_to_delete:
            bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES_ONLY')
        bm.to_mesh(mesh)
    mesh.update()


def clean_base_body_source(copied_source_objs):
    """素体阶段每档阈值都会复制同一张网格并做清理，这里先对源网格清理一次，
    后续各档直接复制清理后的网格（与逐档清理的结果一致，省掉重复的 6 次运算）"""
    for obj in copied_source_objs:
        _clean_temp_mesh(obj.data)


def create_tmp_obj(obj_verts_map, delete_unwanted_flag=True, source_objs=None, tmp_obj_name="TMP",
                   clean=True):
    """创建临时胸部对象；clean=False 表示源网格已清理过（见 clean_base_body_source）"""

    # 获取临时集合，在临时集合中进行模型的处理
    tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)

    # 从原模型中，复制胸部网格，便于后续对这部分内容进行编辑
    tmp_part_objs = []

    # 保留胸部还是保留胸部+躯干
    if delete_unwanted_flag:
        for source_obj, source_vertices in obj_verts_map.items():
            tmp_part_obj = copy_obj(source_obj, tmp_col)
            keep_indices = [v.index for v in source_vertices]
            delete_unwanted_vertices(tmp_part_obj, keep_indices)
            tmp_part_objs.append(tmp_part_obj)
    else:
        for source_obj in source_objs:
            tmp_part_obj = copy_obj(source_obj, tmp_col)
            tmp_part_objs.append(tmp_part_obj)

    deselect_all_objects()
    if len(tmp_part_objs) > 1:
        for tmp_part_obj in tmp_part_objs:
            select_and_activate(tmp_part_obj)
        bpy.ops.object.join()
    else:
        # 不会出现 len(tmp_part_objs) == 0 的情况，前置逻辑已完成拦截处理
        select_and_activate(tmp_part_objs[0])

    tmp_obj = bpy.context.view_layer.objects.active
    tmp_obj.name = tmp_obj_name
    temp_mesh = tmp_obj.data

    # 父级取消关联
    tmp_obj.parent = None

    # 排除形态键干扰
    if tmp_obj.data.shape_keys:
        # 防止 Operator bpy.ops.object.shape_key_remove.poll() failed, context is incorrect
        tmp_obj.active_shape_key_index = 0
        bpy.ops.object.shape_key_remove(all=True, apply_mix=True)

    if clean:
        _clean_temp_mesh(temp_mesh)
    return tmp_col, tmp_obj, temp_mesh


def batch_do_analysis(obj_vgs_map, analysis_type):
    thresholds = ANALYSIS_THRESHOLDS

    # 面数据只收集一次，各档阈值复用，避免每档都做 bmesh 转换与坐标键计算
    face_cache = collect_face_records(obj_vgs_map)
    weight_cache = {}

    results = []
    for index, threshold in enumerate(thresholds):
        result = do_analysis(obj_vgs_map, threshold, index, analysis_type,
                             face_cache=face_cache, weight_cache=weight_cache)
        if result.tmp_obj:
            result.tmp_obj.name = f"{index}_{DEFAULT_TMP_OBJ_NAME}_{threshold}_{result.cup if result.cup else 'None'}"
        results.append(result)

    return results


def filter_results(results):
    """从多次执行的结果中，筛选出最合适的那个结果"""
    # 从原始results中提取cups和cup_inners
    cups = [r.cup if r.cup else "" for r in results]
    cup_inners = [r.cup_inner if r.cup_inner else "" for r in results]

    filtered_results = [r for r in results if r.status == AnalysisStatus.OK and r.cup is not None]
    filtered_results.sort(key=lambda r: r.threshold, reverse=True)

    # 如果存在连续4个相同的cup_index，则直接返回
    ok, result = check_continuous_four(filtered_results)
    if ok:
        return result, cups, cup_inners

    # 若后一个尺码小于前一个尺码，则移除后一个尺码
    if filtered_results:
        valid_results = [filtered_results[0]]
        last_cup_index = filtered_results[0].cup_index

        for r in filtered_results[1:]:
            if r.cup_index >= last_cup_index:
                valid_results.append(r)
                last_cup_index = r.cup_index

        filtered_results = valid_results

    ok, result = check_last_three(filtered_results)
    if ok:
        return result, cups, cup_inners

    lower_bound_index = filtered_results[0].cup_index

    # 获取尺码列表，用于展示
    lower_count = 0
    lower_bound_next_index = None
    lower_count_plus_1 = 0

    for result in filtered_results:
        idx = result.cup_index

        # 统计最小cup的连续数量
        if idx == lower_bound_index:
            lower_count += 1
            continue

        # 最小cup的相邻cup
        if lower_bound_next_index is None:
            lower_bound_next_index = idx

        # 统计相邻cup的连续数量
        if idx == lower_bound_next_index:
            lower_count_plus_1 += 1
        else:
            break

    # 如果最小cup连续出现多次，取稍大的，否则取相邻cup中稍小的
    if lower_count >= lower_count_plus_1:
        return filtered_results[lower_count - 1], cups, cup_inners
    else:
        return filtered_results[lower_count], cups, cup_inners


def check_continuous_four(filtered_results):
    """检查是否存在连续4个相同的cup_index"""
    if len(filtered_results) < 4:
        return False, None

    for i in range(len(filtered_results) - 3):
        cup_index = filtered_results[i].cup_index

        if all(
                filtered_results[i + j].cup_index == cup_index
                for j in range(1, 4)
        ):
            # 取较小的那个，避免深度接近但选择高度更大的结果
            return True, filtered_results[i]

    return False, None


def check_last_three(filtered_results):
    """
    检查候选项末尾连续三个尺码是否满足判断规则。
    借此来减少因胸部边缘权重较低而误判的概率（案例 Tda式初音ミク A ['AA', 'A', 'A', 'C', 'C', 'C']，此时应取C）
    必须在“若后一个尺码小于前一个尺码，则移除后一个尺码”之后执行（案例 克莱妲 C ['C', 'C', '', 'AA', 'AA', 'AA']，此时应取C）
    """
    # 不足三个候选项，直接返回
    if len(filtered_results) < 3:
        return False, None

    # 获取后三项
    last_three = filtered_results[-3:]

    # 后三个不相同则直接返回
    if len({r.cup_index for r in last_three}) != 1:
        return False, None

    # 后三项相同，且仅有三个候选项，返回后三项中的第一个，避免深度接近但选择高度更大的结果
    if len(filtered_results) == 3:
        return True, filtered_results[-3]

    last_cup_index = last_three[0].cup_index

    # 获取前几项中，索引最大值
    max_previous_index = max(r.cup_index for r in filtered_results[:-3])

    # “后三项”与“前几项的最大值”之前不超过3（通常在正负1之内）（案例：幽兰黛尔_不灭星锚 D ['C', 'D', 'D', 'K', 'K', 'K']）
    if abs(last_cup_index - max_previous_index) > 3:
        return False, None

    # 数量小于等于5，返回后三项中的第一个，避免深度接近但选择高度更大的结果
    if len(filtered_results) <= 5:
        return True, filtered_results[-3]

    # 数量等于6
    if len(filtered_results) == 6:
        first_three = filtered_results[:3]

        # 若前三个项不一致，则返回后三项中的第一个，避免深度接近但选择高度更大的结果
        if len({r.cup_index for r in first_three}) != 1:
            return True, filtered_results[-3]

        # 若前三个一致，则返回前三项中的最后一个
        return True, filtered_results[2]

    return False, None


def select_verts(objs, cos):
    current_active_object = bpy.context.active_object

    # 将目标世界坐标转为集合，方便查找
    cos_set = {tuple(round(c, COORD_ROUND_DIGITS) for c in co) for co in cos if co is not None}

    for obj in objs:
        mw = obj.matrix_world

        # 遍历顶点，匹配世界坐标后选中
        for v in obj.data.vertices:
            world_co = mw @ v.co
            key = tuple(round(c, COORD_ROUND_DIGITS) for c in world_co)

            if key in cos_set:
                v.select = True

    # 恢复之前激活的对象
    if current_active_object:
        select_and_activate(current_active_object)


def build_connected_chain(verts):
    """将一组无序的顶点，按照边的连接关系整理成一条有序链（列表）"""

    # 把传入的顶点列表转成集合，方便后续 O(1) 判断某顶点是否属于链
    vert_set = set(verts)

    # -------------------------------------------------------
    # 第一步：寻找链的"端点"作为起点
    #
    # 对于一条开放折线，两个端点各自只有 1 条边连向链内其他顶点；
    # 中间顶点则各有 2 条这样的边。
    # 通过统计每个顶点在 vert_set 内的邻居数量，
    # 找到第一个邻居数 == 1 的顶点作为起点。
    # -------------------------------------------------------
    start = None
    for v in verts:
        # 统计 v 的所有关联边中，对端顶点也在 vert_set 里的边数
        cnt = sum(1 for e in v.link_edges if e.other_vert(v) in vert_set)
        if cnt == 1:
            start = v  # 找到端点，从这里出发
            break

    if start is None:
        # 所有顶点都有 2 个链内邻居，说明这是一个闭合环；
        # 环没有天然端点，随便取第一个顶点开始即可
        start = verts[0]

    # -------------------------------------------------------
    # 第二步：从起点出发，沿边"行走"，依次收集顶点
    #
    # 规则：每次只走向"在 vert_set 内、且不是上一步来的方向"的邻居，
    # 从而保证沿链单向前进，不走回头路。
    # -------------------------------------------------------
    chain = []  # 最终有序链，按遍历顺序存放顶点
    visited = set()  # 记录已访问顶点，防止闭合环时死循环
    curr = start
    prev = None  # 上一个顶点，用于排除回头方向

    while curr and curr not in visited:
        chain.append(curr)
        visited.add(curr)

        nxt = None
        for e in get_sorted_link_edges(curr):
            v = e.other_vert(curr)
            # 条件：对端顶点属于链 且 不是来时的方向
            if v in vert_set and v != prev:
                nxt = v
                break

        prev = curr
        curr = nxt  # 移动到下一个顶点；若无合法邻居则 curr=None，循环自然结束

        # 保险措施：若收集数量远超顶点总数，说明出现意外循环，强制退出
        if len(chain) > len(verts) + 5:
            break

    # 返回按连接顺序排列的顶点列表，可直接用于后续几何计算
    return chain


def cross_z(prev_v, cur_v, next_v):
    """
    计算三个顶点构成折角的"2D 叉积 Z 分量"。

    几何意义：
      以 cur_v 为顶点，prev_v→cur_v 为入边，cur_v→next_v 为出边，
      叉积 Z 分量 > 0 → 向左拐（逆时针）
      叉积 Z 分量 < 0 → 向右拐（顺时针）
      叉积 Z 分量 = 0 → 三点共线（直行）

    公式（2D 叉积）：
      (C - P) × (N - C) 的 Z 分量
      = (Cx-Px)*(Ny-Cy) - (Cy-Py)*(Nx-Cx)
    """
    p, c, n = prev_v.co, cur_v.co, next_v.co
    # 这里只用 x、y 分量，假设折线在 XY 平面上分析
    return (c.x - p.x) * (n.y - c.y) - (c.y - p.y) * (n.x - c.x)


def chain_dominant_sign(chain):
    """
    判断整条链的"主要拐向"是逆时针(+1)还是顺时针(-1)。

    原理：
      统计链中所有中间顶点的叉积符号，正值（逆时针）多则主方向为逆时针，
      反之为顺时针。凹点的定义就是：拐向与主方向相反的顶点。
    """
    pos = neg = 0  # 分别计数"向左拐"和"向右拐"的顶点个数
    for i in range(1, len(chain) - 1):  # 跳过首尾，只看中间顶点
        cz = cross_z(chain[i - 1], chain[i], chain[i + 1])
        if cz > 0:
            pos += 1  # 逆时针拐点
        elif cz < 0:
            neg += 1  # 顺时针拐点
    # 多数派决定主方向
    return 1 if pos >= neg else -1


def build_edge_cache(chain):
    """
    为链上所有边建立字典缓存，加速后续的"查边/删边/加边"操作。

    键（Key）：frozenset({v1, v2})
      frozenset 是不可变集合，不关心顶点顺序：
      frozenset({A, B}) == frozenset({B, A})，适合表示无向边。
    值（Value）：对应的 BMEdge 对象
    """
    cache = {}
    seen_verts = set(chain)  # 链上所有顶点的集合
    for v in seen_verts:
        for e in get_sorted_link_edges(v):
            ov = e.other_vert(v)
            if ov in seen_verts:  # 只缓存"链内边"（两端都在链上）
                cache[frozenset((v, ov))] = e
    return cache


def cached_remove_edge(bm, cache, v1, v2):
    """
    从缓存中移除边，并从 bmesh 中真正删除这条边。

    为什么用 try/except？
      在批量操作中，同一条边可能被多次尝试删除
      （例如两个凹点共享同一条边），第二次删除时 bmesh 会抛出异常，
      用 try/except 忽略即可。
    """
    key = frozenset((v1, v2))
    e = cache.pop(key, None)
    if e:
        try:
            bm.edges.remove(e)
        except Exception:
            pass  # 边已被删除，忽略异常


def cached_new_edge(bm, cache, v1, v2):
    """
    在两顶点之间创建一条新边，并加入缓存。

    先检查缓存，避免重复创建（bmesh 中创建已存在的边会报错）。
    """
    key = frozenset((v1, v2))
    if key not in cache:
        e = bm.edges.new((v1, v2))
        cache[key] = e


def fix_all_concave_verts(bm, chain, dominant, cache):
    """
    一次遍历链，找出所有凹点，批量删除并补边，使轮廓更趋于凸形。

    参数：
      bm       —— 当前 bmesh 对象
      chain    —— 有序顶点链
      dominant —— 主拐向（+1 逆时针 / -1 顺时针）
      cache    —— 边缓存字典（会被原地修改）

    返回：bool，True 表示本次有修改，False 表示链已经没有凹点
    """

    # ------------------------------------------------------------------
    # 第一步：扫描所有中间顶点，标记凹点索引
    #
    # 凹点判定：cross_z 的符号与 dominant 相反
    #
    # 跳过相邻（i += 2）的原因：
    #   若相邻两个顶点都是凹点，同时处理会导致链上出现"空洞"（断裂），
    #   因为第 i 个凹点被删除后，第 i+1 个凹点的前驱已经改变。
    #   跳过相邻凹点，本次只处理"间隔"凹点，下一次迭代再处理剩余的。
    # ------------------------------------------------------------------
    idxs_to_remove = []
    n = len(chain)
    i = 0  # 从0开始覆盖首尾顶点

    while i < n:
        prev = chain[(i - 1) % n]  # 闭合圈取模
        curr = chain[i]
        next_ = chain[(i + 1) % n]
        cz = cross_z(prev, curr, next_)
        if cz != 0 and (1 if cz > 0 else -1) != dominant:
            idxs_to_remove.append(i)
            i += 2  # 跳过相邻凹点
        else:
            i += 1

    # 若待删除的两点恰好位于链的首尾，由于链本身是环状结构，首尾两点实际上是相邻的，因此需要先移除其中一个顶点
    if idxs_to_remove and len(idxs_to_remove) > 1 and idxs_to_remove[0] == 0 and idxs_to_remove[-1] == n - 1:
        idxs_to_remove.pop(0)
    # 记录待删除的顶点用于返回
    values_to_remove = set(chain[i] for i in idxs_to_remove)

    if not idxs_to_remove:
        return False, None

    # ------------------------------------------------------------------
    # 第二步：整理"待删边"和"待加边"
    #
    # 对每个凹点 chain[idx]（设为 cv）：
    #   - 删除边：pv（前驱） ↔ cv、cv ↔ nv（后继）
    #   - 添加边：pv ↔ nv（直接连接前后邻居，绕过凹点）
    #
    # 这样 cv 在逻辑上被从链中"切掉"，虽然顶点本身还在网格里，
    # 但它不再被选中，也不再属于这条链。
    # ------------------------------------------------------------------
    edges_del = []  # [(frozenset_key, BMEdge), ...]
    edges_add = []  # [(v1, v2), ...]
    removed_verts = set()

    for idx in idxs_to_remove:
        # pv, cv, nv = chain[idx - 1], chain[idx], chain[idx + 1]
        pv, cv, nv = chain[(idx - 1) % n], chain[idx], chain[(idx + 1) % n]
        removed_verts.add(cv)

        # 收集待删的两条边（cv 与前驱、cv 与后继）
        for nb in (pv, nv):
            key = frozenset((cv, nb))
            e = cache.get(key)
            if e:
                edges_del.append((key, e))

        # 收集待加的桥接边（前驱 ↔ 后继）
        if frozenset((pv, nv)) not in cache:
            edges_add.append((pv, nv))

    # ------------------------------------------------------------------
    # 第三步：统一执行删边
    # 先全部删完再添加，避免"删了又加同一条边"的冲突
    # ------------------------------------------------------------------
    for key, e in edges_del:
        cache.pop(key, None)  # 从缓存移除
        try:
            bm.edges.remove(e)
        except Exception:
            pass  # 可能已被删除（两个凹点共享边的情况），忽略

    # ------------------------------------------------------------------
    # 第四步：统一添加桥接边
    # ------------------------------------------------------------------
    for v1, v2 in edges_add:
        key = frozenset((v1, v2))
        if key not in cache:
            try:
                e = bm.edges.new((v1, v2))
                cache[key] = e
            except Exception:
                pass  # 边创建失败时（如已存在）忽略

    # ------------------------------------------------------------------
    # 第五步：取消凹点的选中状态
    # 凹点已被"逻辑移除"出链，视觉上也应取消高亮
    # ------------------------------------------------------------------
    for v in removed_verts:
        v.select = False

    return True, values_to_remove


def _segments_cross_xy(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2, eps=1e-6):
    """
    判断两条线段在 2D 平面（XY 平面）上是否真正相交。

    "真正相交"的定义：
      - 两段在各自内部（不含端点）有且仅有一个交点
      - 共享端点不算相交
      - 平行或共线也不算相交

    参数：
      a1=(ax1, ay1), a2=(ax2, ay2) : 线段 A 的两个端点
      b1=(bx1, by1), b2=(bx2, by2) : 线段 B 的两个端点
      eps                          : 浮点容差，用于判断平行以及交点是否在线段内部

    返回：
      True  —— 两段在内部相交（链出现自交）
      False —— 不相交、平行、或仅共享端点

    说明：共享端点的排除由调用方按对象 id 完成（见 find_first_self_intersection），
    本函数只接收坐标，避免在内层循环里反复访问 BMesh 的 .co 属性。

    -------------------------------------------------------
    第一步：把线段写成参数方程
    -------------------------------------------------------
    两段的参数方程（t、u 在 [0,1] 内时点在线段上）：
      P(t) = a1 + t·(a2 - a1)
      Q(u) = b1 + u·(b2 - b1)

    t、u 就是"沿各自线段的归一化位置"：0 在起点，1 在终点，>1 在延长线上。

    -------------------------------------------------------
    第二步：相交 ⇔ 存在 t、u 使 P(t) = Q(u)
    -------------------------------------------------------
    令 P(t) = Q(u)：
      a1 + t·(a2-a1) = b1 + u·(b2-b1)
      t·(a2-a1) - u·(b2-b1) = b1 - a1

    代入缩写：
      (ax,ay) = a2-a1   （线段 A 的方向向量）
      (bx,by) = b2-b1   （线段 B 的方向向量）
      (cx,cy) = b1-a1   （从 a1 指向 b1 的偏移向量）
      t·(a2-a1) - u·(b2-b1) = b1-a1  →  t·(ax,ay) - u·(bx,by) = (cx,cy)

    拆成 x、y 分量，得方程组：
      t·ax - u·bx = cx  ...(1)
      t·ay - u·by = cy  ...(2)

    -------------------------------------------------------
    第三步：计算行列式（叉积的 Z 分量）
    -------------------------------------------------------
      denom = ax*by - ay*bx
            = 向量 A × 向量 B 的 Z 分量

    几何意义：
      denom == 0  →  两段平行（或共线），无唯一交点
      denom != 0  →  两条无限直线有唯一交点，再判断是否落在两段内部

    -------------------------------------------------------
    第四步：用参数方程求交点参数 t 和 u
    -------------------------------------------------------
    【消去 t，求 u】(1)×ay - (2)×ax：
      t·ax·ay - u·bx·ay = cx·ay
      t·ax·ay - u·by·ax = cy·ax
      ─────────────────────────── 相减，t 项消掉
      u·(ax·by - ay·bx) = cx·ay - cy·ax
      u = (cx·ay - cy·ax) / denom

    【消去 u，求 t】(1)×by - (2)×bx：
      t·ax·by - u·bx·by = cx·by
      t·ay·bx - u·by·bx = cy·bx
      ─────────────────────────── 相减，u 项消掉
      t·(ax·by - ay·bx) = cx·by - cy·bx
      t = (cx·by - cy·bx) / denom

    其中 denom = ax·by - ay·bx（两方向向量的 2D 叉积）。

    t、u 的含义：
      t∈(0,1) 且 u∈(0,1) → 交点严格在两段内部（真正相交）
      t 或 u 超出 (0,1)   → 直线相交，但交点在线段延长线上
      denom = 0           → 两段平行，无唯一交点

    -------------------------------------------------------
    第五步：判断交点是否严格落在两段内部
    -------------------------------------------------------
    t ∈ (0, 1)：交点在 A 的内部（不含端点 a1, a2）
    u ∈ (0, 1)：交点在 B 的内部（不含端点 b1, b2）

    用 eps 留出浮点容差，避免极近端点被误判为内部相交。
    两个条件同时满足，才说明两段真正在内部交叉。
    """
    ax = ax2 - ax1
    ay = ay2 - ay1
    bx = bx2 - bx1
    by = by2 - by1

    denom = ax * by - ay * bx
    if abs(denom) < eps:
        return False  # 平行或共线，视为不相交

    cx = bx1 - ax1
    cy = by1 - ay1

    t = (cx * by - cy * bx) / denom  # 交点在线段 A 上的位置比例
    if not (eps < t < 1 - eps):
        return False

    u = (cx * ay - cy * ax) / denom  # 交点在线段 B 上的位置比例
    return eps < u < 1 - eps


def find_first_self_intersection(chain, dirty_segs=None):
    """
    在整条链中扫描，找到第一对相交的线段，返回它们的索引 (i, j)。

    参数：
      chain      —— 有序顶点链
      dirty_segs —— "脏区间"集合（需要复查的线段索引）
                    None 表示全链扫描（首次调用时使用）；
                    有值时只把脏区间的线段作为外层候选，内层仍对全链比较。
                    注意：这是一个优化性取舍，可能漏掉脏区间外的新自交，
                    由外层大循环重建链后的全扫兜底。

    返回：
      (i, j) —— 第一对相交线段的索引（线段 i 是 chain[i]→chain[i+1]）
      None   —— 未找到自交
    """
    n = len(chain)
    if n < 3:
        return None
    candidates = range(n - 1) if dirty_segs is None else sorted(dirty_segs)

    # 一次性取出 XY 坐标与对象标识：内层循环只做浮点比较，不再反复访问 BMesh 属性
    coords = [(v.co.x, v.co.y) for v in chain]
    ids = [id(v) for v in chain]
    eps = 1e-6

    for i in candidates:
        if i >= n - 1:
            continue

        ax1, ay1 = coords[i]
        ax2, ay2 = coords[i + 1]
        aid1 = ids[i]
        aid2 = ids[i + 1]

        # 线段 A 的 XY 包围盒，用于内层快速排除
        aminx, amaxx = (ax1, ax2) if ax1 <= ax2 else (ax2, ax1)
        aminy, amaxy = (ay1, ay2) if ay1 <= ay2 else (ay2, ay1)

        # j 从 i+2 开始：相邻线段天然共享端点，不算自交
        for j in range(i + 2, n - 1):
            bid1 = ids[j]
            bid2 = ids[j + 1]
            # 链中可能存在重复顶点，共享端点的情况仍需排除
            if bid1 == aid1 or bid1 == aid2 or bid2 == aid1 or bid2 == aid2:
                continue

            bx1, by1 = coords[j]
            bx2, by2 = coords[j + 1]

            # 包围盒不相交（留 eps 容差）则两段必不相交
            bminx, bmaxx = (bx1, bx2) if bx1 <= bx2 else (bx2, bx1)
            if bmaxx < aminx - eps or bminx > amaxx + eps:
                continue
            bminy, bmaxy = (by1, by2) if by1 <= by2 else (by2, by1)
            if bmaxy < aminy - eps or bminy > amaxy + eps:
                continue

            if _segments_cross_xy(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2, eps):
                return i, j
    return None


def fix_self_intersection(bm, chain, i, j, cache):
    """
    通过"翻转子链"来解开一个自交。

    原理：
      设两条相交线段为 chain[i]→chain[i+1] 和 chain[j]→chain[j+1]，
      子链 sub = chain[i+1..j]（含两端）。

      现有连接方式（产生交叉）：
        ... → chain[i] → chain[i+1] → ... → chain[j] → chain[j+1] → ...

      翻转子链后（交叉消除）：
        ... → chain[i] → chain[j] → ... → chain[i+1] → chain[j+1] → ...

      翻转操作等价于：
        删除边 chain[i]→chain[i+1] 和 chain[j]→chain[j+1]
        添加边 chain[i]→chain[j] 和 chain[i+1]→chain[j+1]
        将 chain[i+1..j] 区间的顶点列表原地反转

    参数：
      bm    —— bmesh 对象
      chain —— 有序顶点链（会被原地修改）
      i, j  —— 两条相交线段的索引
      cache —— 边缓存（会被同步更新）

    返回：
      dirty —— 受影响的线段索引集合（供下次局部复查使用）
    """
    sub = chain[i + 1: j + 1]
    sub_rev = list(reversed(sub))

    # 删除旧边
    pairs_del = (
            [(chain[i], chain[i + 1])] +
            [(sub[k], sub[k + 1]) for k in range(len(sub) - 1)] +
            [(chain[j], chain[j + 1])]
    )
    for v1, v2 in pairs_del:
        cached_remove_edge(bm, cache, v1, v2)

    # 新边序列
    pairs_add = (
            [(chain[i], sub_rev[0])] +
            [(sub_rev[k], sub_rev[k + 1]) for k in range(len(sub_rev) - 1)] +
            [(sub_rev[-1], chain[j + 1])]
    )
    for v1, v2 in pairs_add:
        cached_new_edge(bm, cache, v1, v2)

    # 更新 chain 中对应位置（原地修改，省去重建链）
    chain[i + 1: j + 1] = sub_rev

    # 返回受影响的线段索引（局部复查用）
    dirty = set(range(max(0, i - 1), min(len(chain) - 1, j + 2)))
    return dirty


def process_bust_line(obj, ring):
    """消除环的自交及凹陷点"""
    max_iter = BUST_LINE_MAX_ITER
    mesh = obj.data

    with bmesh_from_mesh(mesh) as bm:
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        verts = [bm.verts[i] for i in ring if bm.verts[i].link_edges]  # 从传入的索引集合取顶点
        verts = sort_verts(verts)
        for iteration in range(max_iter):
            if len(verts) < 3:
                break

            chain = build_connected_chain(verts)
            if len(chain) < 3:
                break

            cache = build_edge_cache(chain)

            # --- 阶段一：消除所有自交（局部复查） ---
            dirty = None  # 首次全扫
            si_fixed = False  # 标记本轮是否修复了自交
            for _ in range(max_iter):
                cross = find_first_self_intersection(chain, dirty_segs=dirty)
                if not cross:
                    break
                dirty = fix_self_intersection(bm, chain, cross[0], cross[1], cache)
                si_fixed = True

            if si_fixed:
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                continue  # 自交修完后重建链，再判凹点

            # --- 阶段二：批量消除凹点 ---
            dominant = chain_dominant_sign(chain)
            changed, values_to_remove = fix_all_concave_verts(bm, chain, dominant, cache)
            if changed:
                if values_to_remove:
                    # 更新verts范围，避免无法构成链
                    verts[:] = [v for v in verts if v not in values_to_remove]
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
            else:
                break

        bm.to_mesh(mesh)
        mesh.update()
