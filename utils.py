import functools
import math
import re
import time

import addon_utils
import bpy

# 最大重试次数
MAX_RETRIES = 5
# 临时集合名称
TMP_COLLECTION_NAME = "KAFEI临时集合"
# 分析临时集合名称
TMP_ANALYSIS_COLLECTION_NAME = "KAFEI分析临时集合"
# 导入pmx生成的txt文件pattern
TXT_INFO_PATTERN = re.compile(r'(.*)(_e(\.\d{3})?)$')

BREAST_BL_NAME_L = "胸.L"
BREAST_BL_NAME_R = "胸.R"
BREAST_JP_NAME_L = "左胸"
BREAST_JP_NAME_R = "右胸"
UPPER_BODY_NAME = "上半身"
UPPER_BODY2_NAME = "上半身2"
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
    (2, "AA"),
    (4, "A"),
    (6, "B"),
    (8, "C"),
    (10, "D"),
    (12, "E"),
    (14, "F"),
    (16, "G"),
    (18, "H"),
    (20, "I"),
    (22, "J"),
    (24, "K"),
    (26, "L"),
    (28, "M"),
    (30, "N"),
    (32, "O"),
    (34, "P"),
    (36, "Q"),
    (38, "R"),
    (40, "S"),
    (42, "T"),
    (44, "U"),
    (46, "V"),
    (48, "W"),
    (50, "X"),
    (52, "Y"),
    (54, "Z"),
    (float("inf"), None),
]
# cup值到索引的映射
CUP_INDEX_MAP = {v: i for i, (k, v) in enumerate(CUP_LIST)}

def get_cup_index(value):
    return CUP_INDEX_MAP.get(value, -1)

# 胸部尺码判定列表
# 每次递增2.5。
STANDARD_CUP_LIST = [
    (10, "AA"),
    (12.5, "A"),
    (15, "B"),
    (17.5, "C"),
    (20, "D"),
    (22.5, "E"),
    (25, "F"),
    (27.5, "G"),
    (30, "H"),
    (32.5, "I"),
    (35, "J"),
    (37.5, "K"),
    (40, "L"),
    (42.5, "M"),
    (45, "N"),
    (47.5, "O"),
    (50, "P"),
    (52.5, "Q"),
    (55, "R"),
    (57.5, "S"),
    (60, "T"),
    (62.5, "U"),
    (65, "V"),
    (67.5, "W"),
    (70, "X"),
    (72.5, "Y"),
    (75, "Z"),
    (float("inf"), None),
]
# cup值到索引的映射
STANDARD_CUP_INDEX_MAP= {v: i for i, (k, v) in enumerate(STANDARD_CUP_LIST)}

# 最大环数量
MAX_RING_COUNT = 10

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
            clean_scene()
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


def clean_scene():
    # 删除由导入pmx生成的文本（防止找不到脚本）
    text_to_delete_list = []
    for text in bpy.data.texts:
        text_name = text.name
        match = TXT_INFO_PATTERN.match(text_name)
        if match is not None:
            base_text_name = match.group(1)
            if match.group(3) is not None:
                base_text_name = base_text_name + match.group(3)
            base_text = bpy.data.texts.get(base_text_name, None)
            if base_text is not None:
                text_to_delete_list.append(base_text)
                text_to_delete_list.append(text)
    for text_to_delete in text_to_delete_list:
        bpy.data.texts.remove(text_to_delete, do_unlink=True)

    # 删除临时集合内所有物体（pmx文件所在集合）
    if TMP_COLLECTION_NAME in bpy.data.collections:
        collection = bpy.data.collections[TMP_COLLECTION_NAME]
        # 遍历集合中的所有对象
        for obj in list(collection.objects):
            # 从集合中移除对象
            collection.objects.unlink(obj)
            # 从场景中删除对象
            bpy.data.objects.remove(obj, do_unlink=True)
        # 删除临时集合
        bpy.data.collections.remove(collection)

    # 清理递归未使用数据块
    bpy.ops.outliner.orphans_purge(do_recursive=True)


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


def clean_collection(collections):
    """
    清理指定集合中的对象并删除集合。

    参数:
        collections:
            可以是：
            - 集合名称(str)
            - 集合对象(bpy.types.Collection)
            - 集合列表(list/tuple)
    """

    # 转换为列表
    if not isinstance(collections, (list, tuple)):
        collections = [collections]

    for item in collections:
        # 获取集合对象
        if isinstance(item, str):
            collection = get_collection(item)
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
    # 清理未使用数据
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)