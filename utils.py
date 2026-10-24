import functools
import math
import re
import time
from contextlib import contextmanager

import addon_utils
import bmesh
import bpy

# 最大重试次数
MAX_RETRIES = 5
# 临时集合名称
TMP_COLLECTION_NAME = "胸部物理移植临时集合"
# 分析临时集合名称
TMP_ANALYSIS_COLLECTION_NAME = "尺码分析临时集合"
BREAST_BL_NAME_L = "胸.L"
BREAST_BL_NAME_R = "胸.R"
BREAST_JP_NAME_L = "左胸"
BREAST_JP_NAME_R = "右胸"
UPPER_BODY_NAME = "上半身"
NECK_NAME = "首"
# RGBA胸部刚体名称
RGBA_RB_NAMES = ['右胸_後', '右胸_回転', '右胸_前', '右胸_前後', '右胸',
                 '左胸_後', '左胸_回転', '左胸_前', '左胸_前後', '左胸']
# RGBA胸部刚体名称 + 上半身刚体名称
RGBA_RB2_NAMES = ['上半身2_R', '右胸_後', '右胸_回転', '右胸_前', '右胸_前後', '右胸',
                  '上半身2_L', '左胸_後', '左胸_回転', '左胸_前', '左胸_前後', '左胸']
# RGBA胸部Joint名称
RGBA_JOINT_NAMES = [
    "右胸_後1", "右胸_後2", "右胸_回転1", "右胸_前1", "右胸_前2", "右胸_回転2", "右胸_前後1", "右胸_前後2", "右胸",
    "左胸_後1", "左胸_後2", "左胸_回転1", "左胸_前1", "左胸_前2", "左胸_回転2", "左胸_前後1", "左胸_前後2", "左胸", ]
# 双臂刚体名称
LIMB_RB_NAMES = ["右手首", "右手", "右ひじ", "右腕", "左手首", "左手", "左ひじ", "左腕"]
# 四肢 + 躯干 主体刚体碰撞群组  PE 1~16 MMD Tools 0~15
LIMB_RB_GROUP = 13

# 灵敏度设置涉及的刚体名称
SENSITIVITY_RB_NAMES = ["左胸", "右胸", "左胸_後", "右胸_後"]

RB_JOINT_PREFIX_REGEXP = re.compile(r'(?P<prefix>[0-9A-Z]{3}_)(?P<name>.*)')

BREAST_BONE_PATTERN = re.compile(
    r'^胸'  # 开头必须是“胸”
    r'([_上下前後先間親亲変形回転支え基W筋]*)'
    r'([0-9０-９])?'  # 可选的单独数字（半角或全角）
    r'([錘先D])?'
    r'(\.\d{3})?'  # 可选的前置序号
    r'(\.[LR])?'  # 可选的左右标识
    r'(\.\d{3})?'  # 可选的后置序号
    r'$'
)

BREAST_RB_PATTERN = re.compile(
    r'^([_左右胸]*)'  # 开头必须是“胸”
    r'([_上下前後先間親亲変形回転支え基W筋]*)'
    r'([0-9０-９])?'  # 可选的单独数字（半角或全角）
    r'([錘先D])?'
    r'(\.\d{3})?'  # 可选的前置序号
    r'(\.[LR])?'  # 可选的左右标识
    r'(\.\d{3})?'  # 可选的后置序号
    r'$'
)
PHYSICAL_FRAME_NAME = "物理"
COLLISION_MAP = {
    "DEFAULT": "默认",
    "NO_COLLISION": "无碰撞",
}

# 胸部权重阈值
WEIGHT_THRESHOLD = 0.25
# 文件名非法字符
INVALID_CHARS = '<>:"/\\|?*'
# 胸部对象临时名称
DEFAULT_TMP_OBJ_NAME = "DEFAULT_TMP"
BASEBODY_TMP_OBJ_NAME = "BASEBODY_TMP"

# 胸部尺码判定列表
# 每次递增2。
# 如果每次递增设置为2.5，整体罩杯会变小，由于乳首与乳根所对应的胸廓差、背部曲线差其实也很大，所以让每次递增数变小一些，使罩杯的变化更明显一些。
CUP_LIST = [
    (1.6, "AA"),
    (3, "A"),
    (4, "B"),
    (6, "C"),
    (8, "D"),
    (10, "E"),
    (12, "F"),
    (14, "G"),
    (16, "H"),
    (18, "I"),
    (20, "J"),
    (22, "K"),
    (24, "L"),
    (26, "M"),
    (28, "N"),
    (30, "O"),
    (32, "P"),
    (34, "Q"),
    (36, "R"),
    (38, "S"),
    (40, "T"),
    (42, "U"),
    (44, "V"),
    (46, "W"),
    (48, "X"),
    (50, "Y"),
    (52, "Z"),
    (float("inf"), None),
]
# cup值到索引的映射
CUP_INDEX_MAP = {v: i for i, (k, v) in enumerate(CUP_LIST)}


def get_cup_index(value):
    return CUP_INDEX_MAP.get(value, -1)


# 胸部尺码判定列表
# 每次递增2.5。
STANDARD_CUP_LIST = [
    (7.5, "AA"),
    (10, "A"),
    (12.5, "B"),
    (15, "C"),
    (17.5, "D"),
    (20, "E"),
    (22.5, "F"),
    (25, "G"),
    (27.5, "H"),
    (30, "I"),
    (32.5, "J"),
    (35, "K"),
    (37.5, "L"),
    (40, "M"),
    (42.5, "N"),
    (45, "O"),
    (47.5, "P"),
    (50, "Q"),
    (52.5, "R"),
    (55, "S"),
    (57.5, "T"),
    (60, "U"),
    (62.5, "V"),
    (65, "W"),
    (67.5, "X"),
    (70, "Y"),
    (72.5, "Z"),
    (float("inf"), None),
]
# cup值到索引的映射
STANDARD_CUP_INDEX_MAP = {v: i for i, (k, v) in enumerate(STANDARD_CUP_LIST)}

# 最大环数量
MAX_RING_COUNT = 10
# 最大搜索次数
MAX_SEARCH_COUNT = 100000


def get_standard_cup_index(value):
    return CUP_INDEX_MAP.get(value, -1)


# 修正乳首时，顶点关联面的角度超出何种范围时判定为有问题
LINKED_FACES_THRESHOLD = math.radians(130)  # 100度仍不保守，调整至120度，如雷电将军 调整至130度，如苔丝_绮幻

# 模型在MMD与Blender之间的默认缩放值
MODEL_SCALE = 0.08


def find_pmx_root():
    """寻找pmx对应空物体"""
    return next((obj for obj in bpy.context.scene.objects if obj.mmd_type == 'ROOT'), None)


def find_pmx_root_with_child(child):
    """根据child寻找pmx对应空物体"""
    if not child:
        return None

    parent = child
    while parent:
        if parent.mmd_type == 'ROOT':
            return parent
        parent = parent.parent

    return None


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
    if bpy.context.active_object is None:
        return
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None


def show_object(obj):
    """显示物体。在视图取消禁用选择，在视图中取消隐藏，在视图中取消禁用，在渲染中取消禁用"""
    set_visibility(obj, (False, False, False, False))


def hide_object(obj):
    """显示物体。在视图取消禁用选择，在视图中取消隐藏，在视图中取消禁用，在渲染中取消禁用"""
    set_visibility(obj, (True, True, True, True))


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
        'scale': 0.08,
        # 移除未使用的顶点和重复的或无效的面
        'clean_model': True,
        # 其余参数默认。即使ImportHelper存在用户使用过的缓存，参数默认值仍然为其定义时默认值
    }

    for attempt in range(MAX_RETRIES):
        try:
            bpy.ops.mmd_tools.import_model('EXEC_DEFAULT', **params)
            print(bpy.app.translations.pgettext_iface(
                f"Import successful, file: {filepath}, retry count: {attempt}"
            ))
            return True
        except Exception as e:
            print(bpy.app.translations.pgettext_iface(
                f"Import failed, retrying soon, file: {filepath}, error: {e}"
            ))
            time.sleep(1)

    # 所有重试均失败
    raise Exception(bpy.app.translations.pgettext_iface(
        f"Continuous import error, please check. File path: {filepath}"
    ))


def export_pmx(filepath: str) -> bool:
    """导出PMX文件，失败时自动重试"""
    params = {
        'filepath': filepath,
        'scale': 12.5,
    }

    for attempt in range(MAX_RETRIES):
        try:
            bpy.ops.mmd_tools.export_pmx('EXEC_DEFAULT', **params)
            print(bpy.app.translations.pgettext_iface(
                f"Export successful, file: {filepath}, retry count: {attempt}"
            ))
            return True
        except Exception as e:
            print(bpy.app.translations.pgettext_iface(
                f"Export failed, retrying soon, file: {filepath}, error: {e}"
            ))
            time.sleep(1)

    # 全部重试失败后抛出异常
    raise Exception(bpy.app.translations.pgettext_iface(
        f"Continuous export error, please check. File path: {filepath}"
    ))


# 清理时需要跟踪的数据块类型（MMD 导入会创建网格、材质、贴图、动作等）
TRACKED_DATA_BLOCK_TYPES = (
    "meshes",
    "armatures",
    "materials",
    "textures",
    "images",
    "actions",
    "node_groups",
    "shape_keys",
    "texts",
)


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


def remove_pmx(root):
    deselect_all_objects()
    select_and_activate(root)
    do_remove_pmx(root)


def do_remove_pmx(root):
    for child in root.children:
        do_remove_pmx(child)
    bpy.data.objects.remove(root)


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


def get_mmd_tools_version():
    v = get_addon_version("mmd_tools")
    if v > (-1, -1, -1):
        return v
    return get_addon_version("MMD Tools")


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


def get_breast_bones(root):
    """
    获取胸部骨骼列表

    - 通过正则来识别模型中的胸部骨骼。
    - 少女前线2的胸部骨骼单独处理。
    """

    armature = find_pmx_armature(root)
    breast_bones = []
    for b in armature.data.bones:
        b_name = b.name
        if is_dummy_bone(b_name):
            continue
        match = BREAST_BONE_PATTERN.match(b_name)
        if match:
            breast_bones.append(b)
            continue
        match = check_girlsfrontline_breast_bones_and_rbs(b_name)
        if match:
            breast_bones.append(b)

    return breast_bones


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


def clean_temp_data(collections, snapshot=None):
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

        # 遍历集合中的所有对象
        for obj in list(collection.objects):
            # 从集合中移除对象
            collection.objects.unlink(obj)
            # 从场景中删除对象
            bpy.data.objects.remove(obj, do_unlink=True)
        # 删除集合
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
        except:
            pass
    if active_object:
        try:  # 防止 StructRNA of type Object has been removed
            # 可能会出现激活但没选中的情况
            bpy.context.view_layer.objects.active = active_object
        except:
            pass


def format_cm(value, decimals=2, unit=True):
    if value is None:
        return "None"

    result = f"{value * 100:.{decimals}f}"
    return f"{result} cm" if unit else result


def trans_vg(obj, source_vg_name, target_vg_name, factor=1.0, remove_source=True):
    """将顶点权重从源顶点组传递到目标顶点组"""
    deselect_all_objects()
    select_and_activate(obj)
    vgs = obj.vertex_groups

    if source_vg_name == target_vg_name:
        return
    # 源顶点组不存在则跳过
    if source_vg_name not in vgs:
        return
    # 目标顶点组不存在则新建
    if target_vg_name not in vgs:
        obj.vertex_groups.new(name=target_vg_name)

    source_vg = vgs[source_vg_name]
    target_vg = vgs[target_vg_name]
    source_vg_index = source_vg.index
    target_vg_index = target_vg.index

    for vert in obj.data.vertices:
        v_index = vert.index

        # 获取源顶点组权重
        src_weight = 0.0
        for g in vert.groups:
            if g.group == source_vg_index:
                src_weight = g.weight
                break

        if src_weight > 0:
            # 计算传递权重
            transfer_weight = src_weight * factor

            # 获取目标顶点组当前权重
            tgt_weight = 0.0
            for g in vert.groups:
                if g.group == target_vg_index:
                    tgt_weight = g.weight
                    break

            # 将传递权重添加到目标顶点组
            target_vg.add([v_index], tgt_weight + transfer_weight, 'REPLACE')

            # 处理源顶点组权重
            if remove_source:
                # 完全转移，移除源权重
                source_vg.remove([v_index])
            else:
                # 按 factor 减少源权重
                source_vg.add([v_index], src_weight * (1 - factor), 'REPLACE')


def remap_breast_weights(obj):
    """当胸部权重最大值不足1时，重映射胸部权重 如果顶点同时受到BREAST_BL_NAME_L和BREAST_BL_NAME_R影响，那么处理顺序对该点来说无太大影响，此处不是重点"""
    # 获取胸部顶点
    primary, marginal = get_vertices_influenced_by_bones(obj, [BREAST_BL_NAME_L, BREAST_BL_NAME_R])
    breast_v = primary + marginal

    # 获取左右胸部顶点组最大权重
    max_weight_l = 0.0
    max_weight_r = 0.0
    for v in breast_v:
        for g in v.groups:
            group_name = obj.vertex_groups[g.group].name
            if group_name == BREAST_BL_NAME_L:
                max_weight_l = max(max_weight_l, g.weight)
            elif group_name == BREAST_BL_NAME_R:
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
    """一次遍历，同时重映射胸.L和胸.R顶点组，防止一个顶点受两个顶点组影响时，因处理的先后顺序，读取到修改后的值。"""

    vg_l = obj.vertex_groups.get(BREAST_BL_NAME_L)
    vg_r = obj.vertex_groups.get(BREAST_BL_NAME_R)

    if vg_l is None or vg_r is None:
        return

    index_l = vg_l.index
    index_r = vg_r.index

    for v in vertices:

        weight_l = 0.0
        weight_r = 0.0

        other_groups = []

        # 读取原始权重
        for g in v.groups:

            group = obj.vertex_groups[g.group]

            if g.group == index_l:
                weight_l = g.weight
                continue

            if g.group == index_r:
                weight_r = g.weight
                continue

            if group.lock_weight:
                continue

            other_groups.append((group, g.weight))

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

            for vg, weight in other_groups:
                ratio = weight / other_total
                new_weight = max(weight - increase * ratio, 0)

                vg.add([v.index], new_weight, 'REPLACE')

        # 最后一次性写入左右胸
        if new_weight_l != weight_l:
            vg_l.add([v.index], new_weight_l, 'REPLACE')

        if new_weight_r != weight_r:
            vg_r.add([v.index], new_weight_r, 'REPLACE')


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
