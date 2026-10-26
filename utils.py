import functools
import math
import re
import time
from contextlib import contextmanager

import addon_utils
import bmesh
import bpy

from .constants import (
    BREAST_BL_NAME_L, BREAST_BL_NAME_R, BREAST_BONE_PATTERN, CUP_INDEX_MAP,
    MAX_RETRIES, MODEL_SCALE, RETRY_DELAY, STANDARD_CUP_INDEX_MAP, TASK_ANALYSIS,
    TASK_SET_RGBA, TMP_OBJ_PROP, TRACKED_DATA_BLOCK_TYPES, WEIGHT_THRESHOLD,
)

# 批处理进度：存放在模块级变量中，而不是 Scene 属性里，
# 这样进程崩溃/强制退出时不会把“处理中”状态写进 .blend
_BATCH_PROGRESS = {
    TASK_SET_RGBA: {"active": False, "factor": 0.0, "text": "", "current": 0, "total": 0},
    TASK_ANALYSIS: {"active": False, "factor": 0.0, "text": "", "current": 0, "total": 0},
}


def get_batch_progress(task):
    """当前批处理进度（面板绘制用）"""
    return _BATCH_PROGRESS[task]


def set_batch_progress(task, active, factor=0.0, text="", current=0, total=0):
    """更新批处理进度；active=False 表示结束，面板不再画进度条"""
    state = _BATCH_PROGRESS[task]
    state["active"] = bool(active)
    state["factor"] = min(max(float(factor), 0.0), 1.0)
    state["text"] = text
    state["current"] = current
    state["total"] = total


def tag_redraw(context):
    """标记 3D 视图重画：进度条在侧边栏里，不主动重绘就要等下次界面刷新"""
    window = getattr(context, "window", None)
    if window is None or window.screen is None:
        return
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def get_cup_index(value):
    return CUP_INDEX_MAP.get(value, -1)


def get_standard_cup_index(value):
    return STANDARD_CUP_INDEX_MAP.get(value, -1)


def find_pmx_armature(pmx_root):
    return next((child for child in pmx_root.children if child.type == 'ARMATURE'), None)


def find_pmx_objects(pmx_armature):
    return list((child for child in pmx_armature.children if child.type == 'MESH' and child.mmd_type == 'NONE'))


def find_rigid_body_parent(root):
    """寻找刚体对象"""
    return next(filter(lambda o: o.type == 'EMPTY' and o.mmd_type == 'RIGID_GRP_OBJ', root.children), None)


def find_joint_parent(root):
    return next(filter(lambda o: o.type == 'EMPTY' and o.mmd_type == 'JOINT_GRP_OBJ', root.children), None)


def find_rigid_body(rb_parent, name_j):
    """按 name_j 查找刚体，找不到时返回 None"""
    if rb_parent is None:
        return None
    return next((rb for rb in rb_parent.children if rb.mmd_rigid.name_j == name_j), None)


def select_and_activate(obj):
    """选中并激活物体"""
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except RuntimeError:  # RuntimeError: 错误: 物体 'xxx' 不在视图层 'ViewLayer'中, 所以无法选中!
        pass


def deselect_all_objects():
    """对场景中的选中对象和活动对象取消选择"""
    # 没有活动对象时无法切换模式，但仍要取消选择
    active_object = bpy.context.active_object
    if active_object is not None and active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    # 只遍历当前已选对象逐个取消选中：bpy.ops.object.select_all 会触发一次
    # 视图层更新，在批量流程里（几十次调用）开销明显
    for obj in list(bpy.context.selected_objects):
        try:
            obj.select_set(False)
        except RuntimeError:  # 物体不在视图层中，无法取消选中
            pass
    bpy.context.view_layer.objects.active = None


def show_object(obj):
    """显示物体。在视图取消禁用选择，在视图中取消隐藏，在视图中取消禁用，在渲染中取消禁用"""
    set_visibility(obj, (False, False, False, False))


def set_visibility(obj, visibility):
    """设置Blender物体的可见性相关属性"""
    # 如果不在当前视图层，则跳过，如"在视图层中排除该集合"的情况下
    view_layer = bpy.context.view_layer
    # 成员资格测试（Python 会调用对象的 __eq__ 方法。）
    if obj.name not in view_layer.objects:
        return
    # 是否可选
    obj.hide_select = visibility[0]
    # 是否在视图中隐藏
    obj.hide_set(visibility[1])
    # 是否在视图中禁用
    obj.hide_viewport = visibility[2]
    # 是否在渲染中禁用
    obj.hide_render = visibility[3]


def is_visible_in_viewport(obj):
    """
    物体是否真的显示在当前视图里

    visible_get() 一次覆盖所有「看不见」的情况：
    - 物体自己被隐藏（hide_set）或被禁用（hide_viewport）
    - 所在集合被隐藏、被禁用或在视图层里被排除
    - 根本没链进场景 / 不在当前视图层

    物体同时挂在多个集合上时，只要还有一个集合可见，就算可见。
    """
    return obj.visible_get()


@contextmanager
def bmesh_from_mesh(mesh):
    """从 Mesh 创建 bmesh，退出时自动释放"""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        yield bm
    finally:
        bm.free()


def import_pmx(filepath: str) -> bool:
    """导入PMX文件，失败时自动重试"""
    params = {
        'filepath': filepath,
        'scale': MODEL_SCALE,
        # 移除未使用的顶点和重复的或无效的面
        'clean_model': True,
        # 其余参数默认。即使ImportHelper存在用户使用过的缓存，参数默认值仍然为其定义时默认值
    }

    for attempt in range(MAX_RETRIES):
        try:
            bpy.ops.mmd_tools.import_model('EXEC_DEFAULT', **params)
            print(f"导入成功，文件：{filepath}，重试次数：{attempt}")
            return True
        except Exception as e:
            print(f"导入失败，即将重试，文件：{filepath}，错误：{e}")
            time.sleep(RETRY_DELAY)

    # 所有重试均失败
    raise Exception(f"持续导入异常，请检查。文件路径：{filepath}")


def export_pmx(filepath: str) -> bool:
    """导出PMX文件，失败时自动重试"""
    params = {
        'filepath': filepath,
        'scale': 1 / MODEL_SCALE,
    }

    for attempt in range(MAX_RETRIES):
        try:
            bpy.ops.mmd_tools.export_pmx('EXEC_DEFAULT', **params)
            print(f"导出成功，文件：{filepath}，重试次数：{attempt}")
            return True
        except Exception as e:
            print(f"导出失败，即将重试，文件：{filepath}，错误：{e}")
            time.sleep(RETRY_DELAY)

    # 全部重试失败后抛出异常
    raise Exception(f"持续导出异常，请检查。文件路径：{filepath}")


def snapshot_data_blocks():
    """记录当前已存在的数据块，供后续只清理本次新增的无用户数据"""
    data_pointers = {}

    for name in TRACKED_DATA_BLOCK_TYPES:
        if not hasattr(bpy.data, name):
            continue

        data_blocks = getattr(bpy.data, name)
        pointers = {block.as_pointer() for block in data_blocks}
        data_pointers[name] = pointers

    return data_pointers


def purge_new_orphan_data(snapshot):
    """只删除快照之后新增的数据块，避免误删用户已有数据"""
    if not snapshot:
        return

    for name, before in snapshot.items():
        blocks = getattr(bpy.data, name)
        for block in list(blocks):
            if block.as_pointer() in before:
                continue
            # 本次操作新增的文本均为导入产物，直接删除（Text 默认带 fake user）
            if name == "texts":
                try:
                    blocks.remove(block)
                except Exception:
                    pass
                continue
            if block.users != 0:
                continue
            if getattr(block, "use_fake_user", False):
                continue
            try:
                blocks.remove(block)
            except Exception:
                pass


def find_layer_collection_by_name(layer_collection, collection_name):
    """递归查询集合"""
    # 如果当前集合名称匹配
    if layer_collection.name == collection_name:
        return layer_collection

    # 遍历子集合，递归查找
    for child in layer_collection.children:
        result = find_layer_collection_by_name(child, collection_name)
        if result:
            return result

    # 如果没有找到匹配的集合，返回 None
    return None


def get_collection(collection_name):
    """获取指定名称集合，没有则新建，然后激活"""
    if collection_name in bpy.data.collections:
        collection = bpy.data.collections[collection_name]
    else:
        collection = bpy.data.collections.new(collection_name)

    # 新建和原有集合都可能未被link
    if collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(collection)

    layer_collection = find_layer_collection_by_name(bpy.context.view_layer.layer_collection, collection_name)
    bpy.context.view_layer.active_layer_collection = layer_collection
    return collection


def get_addon_version(name):
    for addon in addon_utils.modules():
        if addon.bl_info.get('name') == name:
            return addon.bl_info.get('version', (-1, -1, -1))
    return -1, -1, -1


def is_mmd_tools_enabled():
    """
    校验mmd_tools是否开启，addon.module分别为：
    3.x版本 为 mmd_tools
    4.2版本 临时为 bl_ext.user_default.mmd_tools
    4.3版本及以后 bl_ext.blender_org.mmd_tools
    """
    for addon in bpy.context.preferences.addons:
        if addon.module.endswith("mmd_tools"):
            return True
    return False


def int2base(x, base, width=0):
    """
    Method to convert an int to a base
    Source: http://stackoverflow.com/questions/2267362
    """
    import string
    digs = string.digits + string.ascii_uppercase
    assert (2 <= base <= len(digs))
    digits, negtive = '', False
    if x <= 0:
        if x == 0:
            return '0' * max(1, width)
        x, negtive, width = -x, True, width - 1
    while x:
        digits = digs[x % base] + digits
        x //= base
    digits = '0' * (width - len(digits)) + digits
    if negtive:
        digits = '-' + digits
    return digits


def is_dummy_bone(name):
    return name.startswith("_dummy_") or name.startswith("_shadow_")


def find_ancestor(obj):
    ancestor = obj
    while ancestor.parent is not None:
        ancestor = ancestor.parent
    return ancestor


def find_objs(root, obj_type='MESH'):
    """递归查找 root 及其所有子物体中指定类型的物体（Mesh 默认排除刚体）"""
    objs = []

    def is_rigidbody(obj):
        return obj.rigid_body is not None

    def recurse(obj):
        if obj.type == obj_type:
            # 如果是 Mesh，排除刚体
            if obj_type == 'MESH' and is_rigidbody(obj):
                pass
            else:
                objs.append(obj)

        for child in obj.children:
            recurse(child)

    recurse(root)
    return objs


def get_breast_bones_map(armatures, extra_bones):
    """
    获取骨架与胸部骨骼的映射

    - 通过正则来识别模型中的胸部骨骼。
    - 少女前线2的胸部骨骼单独处理。

    Returns:
        {
            armature1: [bone1, bone2, ...],
            armature2: [bone3, bone4, ...],
            ...
        }
    """

    if not isinstance(armatures, (list, tuple, set)):
        armatures = [armatures]

    if isinstance(extra_bones, str):
        extra_bones = [
            name.strip()
            for name in re.split(r"[,，]+", extra_bones)
            if name.strip()
        ]
    else:
        extra_bones = extra_bones

    breast_bones_map = {}

    for armature in armatures:
        breast_bones = []

        for bone in armature.data.bones:
            b_name = bone.name

            if is_dummy_bone(b_name):
                continue

            if b_name in extra_bones:
                breast_bones.append(bone)
                continue

            if BREAST_BONE_PATTERN.match(b_name):
                breast_bones.append(bone)
                continue

            if check_girlsfrontline_breast_bones_and_rbs(b_name):
                breast_bones.append(bone)

        if breast_bones:
            breast_bones_map[armature] = breast_bones

    return breast_bones_map


# 少女前线2单独校验
def check_girlsfrontline_breast_bones_and_rbs(b_name):
    if "chest_r" in b_name.lower():
        return True
    if "chest_l" in b_name.lower():
        return True
    if "bone" in b_name.lower() and "ches" in b_name.lower():
        return True
    return False


def copy_obj(obj, col=None):
    new_mesh = obj.data.copy()
    new_obj = obj.copy()
    new_obj.data = new_mesh
    if not col:
        col = obj.users_collection[0]
    col.objects.link(new_obj)
    return new_obj


def timeit(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()

        result = func(*args, **kwargs)

        end = time.perf_counter()

        print(f"{func.__name__} 执行耗时: {end - start:.6f} 秒")

        return result

    return wrapper


def is_temp_obj(obj):
    """是否为尺码分析保留的临时对象"""
    return obj is not None and TMP_OBJ_PROP in obj


def remove_obj(obj):
    """
    删除对象，并清理其无人使用的网格数据，避免留下孤儿网格

    remove_tmp=False 时，临时对象会被保留。
    再次执行时，虽然会删除临时对象，但孤儿 Mesh 数据仍会被计入快照，无法随之清理，因此该方法会同时删除临时对象及其关联的孤儿 Mesh 数据。
    """
    data = getattr(obj, "data", None)
    bpy.data.objects.remove(obj, do_unlink=True)
    # 临时对象的网格都是独立副本，对象删除后数据块无人使用即可一并删除
    if isinstance(data, bpy.types.Mesh) and data.users == 0 and not data.use_fake_user:
        bpy.data.meshes.remove(data)


def clean_temp_data(collections, snapshot=None, only_marked=True):
    """
    清理指定集合及其对象，并可选清理本次新增的数据块与导入文本。

    参数:
        collections:
            可以是：
            - 集合名称(str)
            - 集合对象(bpy.types.Collection)
            - 集合列表(list/tuple)
        snapshot:
            操作前记录的数据块快照；提供时只清理本次新增的数据
        only_marked:
            默认只删除带临时标记的对象；False 时删除集合内全部对象（RGBA 流程使用）
    """

    # 转换为列表
    if not isinstance(collections, (list, tuple)):
        collections = [collections]

    for item in collections:
        # 获取集合对象（仅查询，不存在时跳过，避免创建集合的副作用）
        if isinstance(item, str):
            collection = bpy.data.collections.get(item)
        else:
            collection = item

        if collection is None:
            continue

        # 只删除带临时标记的对象，集合中用户自己的对象保留
        for obj in list(collection.objects):
            if only_marked and not is_temp_obj(obj):
                continue
            # 删除对象并清理其无人使用的网格数据
            remove_obj(obj)
        # 集合空了才删除，仍有用户对象时保留集合
        if not collection.objects:
            bpy.data.collections.remove(collection)

    # 只清理本次新增的数据块，避免影响用户已有数据
    if snapshot is not None:
        purge_new_orphan_data(snapshot)


def restore_selection(selected_objects, active_object):
    """ 恢复选中状态"""
    deselect_all_objects()
    for obj in selected_objects:
        try:  # 防止 StructRNA of type Object has been removed
            select_and_activate(obj)
        except (ReferenceError, RuntimeError):
            pass
    if active_object:
        try:  # 防止 StructRNA of type Object has been removed
            # 可能会出现激活但没选中的情况
            bpy.context.view_layer.objects.active = active_object
        except (ReferenceError, RuntimeError):
            pass


def format_cm(value, decimals=2, unit=True):
    if value is None:
        return "None"

    result = f"{value * 100:.{decimals}f}"
    return f"{result} cm" if unit else result


def trans_vg(obj, source_vg_name, target_vg_name, factor=1.0, remove_source=True):
    """
    将顶点权重从源顶点组传递到目标顶点组。
    一次遍历同时读取源/目标权重，减少顶点组查询。
    """
    vgs = obj.vertex_groups

    if source_vg_name == target_vg_name:
        return
    # 源顶点组不存在则跳过
    if source_vg_name not in vgs:
        return
    # 目标顶点组不存在则新建
    if target_vg_name not in vgs:
        vgs.new(name=target_vg_name)

    source_vg = vgs[source_vg_name]
    target_vg = vgs[target_vg_name]
    source_index = source_vg.index
    target_index = target_vg.index

    for vert in obj.data.vertices:
        src_weight = 0.0
        tgt_weight = 0.0
        for g in vert.groups:
            if g.group == source_index:
                src_weight = g.weight
            elif g.group == target_index:
                tgt_weight = g.weight

        if src_weight <= 0:
            continue

        # 将传递权重添加到目标顶点组
        target_vg.add([vert.index], tgt_weight + src_weight * factor, 'REPLACE')

        # 处理源顶点组权重
        if remove_source:
            # 完全转移，移除源权重
            source_vg.remove([vert.index])
        else:
            # 按 factor 减少源权重
            source_vg.add([vert.index], src_weight * (1 - factor), 'REPLACE')


def remap_breast_weights(obj):
    """当胸部权重最大值不足1时，重映射胸部权重 如果顶点同时受到BREAST_BL_NAME_L和BREAST_BL_NAME_R影响，那么处理顺序对该点来说无太大影响，此处不是重点"""
    # 获取胸部顶点
    primary, marginal = get_vertices_influenced_by_bones(obj, [BREAST_BL_NAME_L, BREAST_BL_NAME_R])
    breast_v = primary + marginal

    vg_l = obj.vertex_groups.get(BREAST_BL_NAME_L)
    vg_r = obj.vertex_groups.get(BREAST_BL_NAME_R)
    if vg_l is None or vg_r is None:
        return
    index_l = vg_l.index
    index_r = vg_r.index

    # 获取左右胸部顶点组最大权重（按索引比较，避免每个权重都做一次顶点组查询）
    max_weight_l = 0.0
    max_weight_r = 0.0
    for v in breast_v:
        for g in v.groups:
            if g.group == index_l:
                max_weight_l = max(max_weight_l, g.weight)
            elif g.group == index_r:
                max_weight_r = max(max_weight_r, g.weight)

    # 左胸或右胸最大权重不足1，进行重映射
    do_remap_breast_weights(obj, breast_v, max_weight_l, max_weight_r)


def get_vertices_influenced_by_bones(obj, bone_names):
    """
    按骨骼权重将顶点分为两组：
    - primary:  总权重 >  WEIGHT_THRESHOLD（主要受影响顶点）
    - marginal: 总权重 在 (0, WEIGHT_THRESHOLD] 之间（轻微受影响顶点）
    """
    primary = []  # 权重显著的顶点
    marginal = []  # 权重微小但非零的顶点

    bone_name_set = set(bone_names)
    vg_index_to_name = {i: vg.name for i, vg in enumerate(obj.vertex_groups)}

    for v in obj.data.vertices:
        total_weight = sum(
            g.weight
            for g in v.groups
            if vg_index_to_name.get(g.group) in bone_name_set
        )

        if total_weight > WEIGHT_THRESHOLD:
            primary.append(v)
        elif total_weight > 0:  # 隐含 total_weight <= WEIGHT_THRESHOLD
            marginal.append(v)

    return primary, marginal


def do_remap_breast_weights(obj, vertices, max_weight_l, max_weight_r):
    """一次遍历，同时重映射胸.L和胸.R顶点组，防止一个顶点受两个顶点组影响时，因处理的先后顺序，读取到修改后的值。
    使用一次 bmesh 写入，避免逐顶点调用 VertexGroup.add 触发网格更新。"""

    vg_l = obj.vertex_groups.get(BREAST_BL_NAME_L)
    vg_r = obj.vertex_groups.get(BREAST_BL_NAME_R)

    if vg_l is None or vg_r is None:
        return

    index_l = vg_l.index
    index_r = vg_r.index
    # 锁定权重的顶点组不参与权重的让渡
    locked_indices = {vg.index for vg in obj.vertex_groups if vg.lock_weight}
    target_indices = [v.index for v in vertices]

    with bmesh_from_mesh(obj.data) as bm:
        # deform：bmesh 的“顶点权重层”集合。每个 deform 层对应网格里的一组顶点组权重（普通网格通常只有一层）。
        #.verify() 返回该层； 如果还没有这一层就创建它 （get-or-create）。
        deform = bm.verts.layers.deform.verify()
        bm.verts.ensure_lookup_table()

        for v_index in target_indices:
            # 拿到这个顶点的权重容器BMDeformVert，本质是一个{顶点组索引: 权重}的字典 。
            weights = bm.verts[v_index][deform]

            weight_l = weights.get(index_l, 0.0)
            weight_r = weights.get(index_r, 0.0)

            other_groups = [
                (index, weight) for index, weight in weights.items()
                if index != index_l and index != index_r
                and index not in locked_indices and weight > 0
            ]

            new_weight_l = weight_l
            new_weight_r = weight_r

            # 左胸映射
            if 0 < max_weight_l < 1.0 and weight_l > 0:
                new_weight_l = min(weight_l / max_weight_l, 1.0)

            # 右胸映射
            if 0 < max_weight_r < 1.0 and weight_r > 0:
                new_weight_r = min(weight_r / max_weight_r, 1.0)

            increase_l = new_weight_l - weight_l
            increase_r = new_weight_r - weight_r
            increase = increase_l + increase_r
            if increase <= 0:
                continue

            other_total = sum(weight for _, weight in other_groups)

            # 其它权重不足时，按比例缩小左右胸增加量，暂未发现此类案例
            if increase > other_total:
                if other_total > 0:
                    scale = other_total / increase

                    increase_l *= scale
                    increase_r *= scale

                    new_weight_l = weight_l + increase_l
                    new_weight_r = weight_r + increase_r

                    increase = other_total
                else:
                    # 没有其它权重可以让渡，胸部保持原权重
                    new_weight_l = weight_l
                    new_weight_r = weight_r
                    increase = 0

            # 从其它权重中扣除
            if other_total > 0:
                for index, weight in other_groups:
                    weights[index] = max(weight - increase * (weight / other_total), 0.0)

            # 最后一次性写入左右胸
            if new_weight_l != weight_l:
                weights[index_l] = new_weight_l

            if new_weight_r != weight_r:
                weights[index_r] = new_weight_r

        bm.to_mesh(obj.data)
    obj.data.update()


def adjust_ang_limits(target, cup_index):
    """根据胸部尺码调整角度限制"""

    if cup_index is None:
        return

    def scale_limits(scale):
        target.limit_ang_x_lower /= scale
        target.limit_ang_x_upper /= scale
        target.limit_ang_y_lower /= scale
        target.limit_ang_y_upper /= scale
        target.limit_ang_z_lower /= scale
        target.limit_ang_z_upper /= scale

    def set_min_limits(value):
        limit = math.radians(value)
        target.limit_ang_x_lower = limit
        target.limit_ang_x_upper = limit
        target.limit_ang_y_lower = limit
        target.limit_ang_y_upper = limit
        target.limit_ang_z_lower = limit
        target.limit_ang_z_upper = limit

    if cup_index <= get_cup_index("B"):
        # 尺码B及以下，降低5倍
        scale_limits(5)

        if cup_index == get_cup_index("A"):
            # 尺码A及以下，再降低5倍
            scale_limits(5)

        elif cup_index == get_cup_index("AA"):
            # 尺码AA，限制到0.5度
            set_min_limits(0.5)
