import re
import time

import addon_utils
import bpy

# 最大重试次数
MAX_RETRIES = 5
# 临时集合名称
TMP_COLLECTION_NAME = "KAFEI临时集合"
# 导入pmx生成的txt文件pattern
TXT_INFO_PATTERN = re.compile(r'(.*)(_e(\.\d{3})?)$')


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
    v = get_mmd_tools_version()
    params = {
        'filepath': filepath,
        'scale': 12.5,
    }
    if v < (4, 5, 2):
        params['copy_textures'] = False
    else:
        params['copy_textures_mode'] = 'NONE'

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
