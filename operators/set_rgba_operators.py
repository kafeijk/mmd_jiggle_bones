import math
import os
from collections import defaultdict, OrderedDict
from datetime import datetime

import bmesh
import mathutils
from mathutils.bvhtree import BVHTree

from ..utils import *

BREAST_BL_NAME_L = "胸.L"
BREAST_BL_NAME_R = "胸.R"
BREAST_JP_NAME_L = "左胸"
BREAST_JP_NAME_R = "右胸"
UPPER_BODY_NAME = "上半身"
UPPER_BODY2_NAME = "上半身2"
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
# 胸部碰撞群组
BREAST_RB_GROUP = 14  # 15-1

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


# 少女前线2单独校验
def check_girlsfrontline_breast_bones_and_rbs(b_name):
    if "chest_r" in b_name.lower():
        return True
    if "chest_l" in b_name.lower():
        return True
    if "bone" in b_name.lower() and "ches" in b_name.lower():
        return True
    return False


def get_mmd_info(root):
    armature = find_pmx_armature(root)
    objs = find_pmx_objects(armature)
    joint_parent = find_joint_parent(root)
    rb_parent = find_rigid_body_parent(root)
    return armature, objs, joint_parent, rb_parent


def copy_rb(rb):
    new_mesh = rb.data.copy()
    new_rb = rb.copy()
    new_rb.data = new_mesh
    col = rb.users_collection[0]
    col.objects.link(new_rb)
    return new_rb


class SetRgbaOperator(bpy.types.Operator):
    bl_idname = "mmd_jiggle_tools.set_rgba"  # 引用时的唯一标识符
    bl_label = "Execute"  # 显示名称（F3搜索界面，不过貌似需要注册，和panel中显示的内容区别开）
    bl_description = ("RGBA式胸部物理移植\n"
                      "生成的模型无法在Blender、MMM、NexGiMa中直接进行烘焙\n"
                      "如需烘焙，请使用MMD桥生成带物理的VMD文件，或采用ABC流程")
    bl_options = {'REGISTER', 'UNDO'}  # 启用撤销功能

    def execute(self, context):
        self.main(context)
        return {'FINISHED'}  # 让Blender知道操作已成功完成

    def main(self, context):
        scene = context.scene
        props = scene.mmd_jiggle_tools_set_rgba
        if not self.check_props(props):
            return
        self.batch_process(self.set_rgba, props)

    def batch_process(self, func, props):
        start_time = time.time()
        batch = props.batch
        abs_path = bpy.path.abspath(batch.directory)
        name_msg_map = OrderedDict()

        # 搜索模型文件
        file_list = recursive_search(props)
        file_count = len(file_list)

        # 批量处理
        for index, filepath in enumerate(file_list):
            file_start = time.time()
            name, status, msg = func(props, f_path=filepath)
            if status == "ERROR":
                name_msg_map[name] = msg

            file_name = os.path.basename(filepath)
            elapsed_file = time.time() - file_start
            elapsed_total = time.time() - start_time
            print(f'文件“{file_name}”处理完成，进度{index + 1}/{file_count}'
                  f'(当前耗时{elapsed_file:.2f}s，总耗时{elapsed_total:.2f}s)')

        # 汇总结果
        total_time = time.time() - start_time
        if name_msg_map:
            combined_msg = "\n".join(f"{name} - {msg}" for name, msg in name_msg_map.items())
            msg = (f"{file_count - len(name_msg_map)}/{file_count} 个文件已处理完成"
                   f"（总耗时 {total_time:.2f}s）。点击查看详细报告 ↑↑↑")
            print(combined_msg)
            print(msg)
            self.report({'WARNING'}, f"{combined_msg}")
            self.report({'WARNING'}, msg)
        else:
            msg = f"目录“{abs_path}”处理完成（总耗时{total_time:.2f}s）"
            self.report({'INFO'}, msg)

    def check_props(self, props):
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "MMD Tools plugin is not enabled!")
            return False

        batch = props.batch
        if not check_batch_props(self, batch):
            return False

        return True

    def set_rgba(self, props, f_path=None):
        factor = round_to_two_decimals(props.factor)
        rb_scale_factor = round_to_two_decimals(props.rb_scale_factor)
        filepath = f_path
        collision = props.collision

        # 防止MMD Tools插件的导入Bug，这里需将当前帧调整为0或1
        bpy.context.scene.frame_current = 0
        # 获取临时集合，在临时集合中进行模型的处理
        get_collection(TMP_COLLECTION_NAME)

        # 导入源模型
        import_pmx(filepath)
        root = bpy.context.active_object
        armature, objs, joint_parent, rb_parent = get_mmd_info(root)
        obj = objs[0]

        # 获取源模型名称
        abs_path = bpy.path.abspath(filepath)
        file_dir = os.path.dirname(bpy.path.abspath(filepath))
        file_name = os.path.basename(abs_path)
        name, ext = os.path.splitext(file_name)

        # 获取源模型胸部骨骼列表
        breast_bones = get_breast_bones(root)
        if not breast_bones:
            clean_tmp_collection()
            return name, "ERROR", "源模型中未找到胸部骨骼"
        breast_names = [b.name for b in breast_bones]

        # 筛选源模型胸部骨骼中的水平胸部骨骼，用于计算位置
        horizontal_bones = filter_horizontal_bones(armature, breast_bones)
        # 从源模型胸部顶点中筛选权重大于WEIGHT_THRESHOLD的顶点，作为胸部网格范围，用于定位伪胸部骨骼的坐标
        influenced_verts = get_vertices_influenced_by_bones(obj, [b.name for b in breast_bones])
        if not influenced_verts:
            clean_tmp_collection()
            return name, "ERROR", f"源模型中胸部顶点权重均小于{WEIGHT_THRESHOLD}，无法获取有效胸部网格范围"

        # 校验源模型是否存在名为“上半身2”的骨骼
        if not any(pb.name == UPPER_BODY2_NAME for pb in armature.pose.bones):
            clean_tmp_collection()
            return name, "ERROR", f"源模型中未找到名称为“{UPPER_BODY2_NAME}”的骨骼"

        # 获取源模型“物理”显示枠索引
        frames = root.mmd_root.display_item_frames
        physics_frame_index = next((i for i, frame in enumerate(frames) if frame.name == PHYSICAL_FRAME_NAME), -1)

        # 获取源模型胸部饰品信息
        accessory_breast_rel_map, kept_joints = get_accessory_info(armature, breast_bones, breast_names, joint_parent,
                                                                   rb_parent)

        # 导入RGBA胸部
        rgba_file_l = os.path.join(os.path.dirname(os.path.dirname(__file__)), "externals", "RGBA_L.pmx")
        import_pmx(rgba_file_l)
        root_l = bpy.context.active_object
        armature_l, objs_l, joint_parent_l, rb_parent_l = get_mmd_info(root_l)
        rgba_file_r = os.path.join(os.path.dirname(os.path.dirname(__file__)), "externals", "RGBA_R.pmx")
        import_pmx(rgba_file_r)
        root_r = bpy.context.active_object
        armature_r, objs_r, joint_parent_r, rb_parent_r = get_mmd_info(root_r)

        # 移除RGBA胸部网格对象
        for breast_obj in objs_l + objs_r:
            bpy.data.objects.remove(breast_obj)

        # 获取RGBA胸部中左右胸骨
        bone_l = armature_l.pose.bones.get(BREAST_BL_NAME_L)
        if not bone_l:
            clean_tmp_collection()
            raise RuntimeError(f"未在胸部素材中找到{BREAST_BL_NAME_L}骨骼")
        bone_r = armature_r.pose.bones.get(BREAST_BL_NAME_R)
        if not bone_r:
            clean_tmp_collection()
            raise RuntimeError(f"未在胸部素材中找到{BREAST_BL_NAME_R}骨骼")

        # 获取伪胸部骨骼的坐标
        dummy_head_lo_l, dummy_head_lo_r, dummy_tail_lo_l, dummy_tail_lo_r, x_r, z_r = get_dummy_breast(
            armature, breast_bones, horizontal_bones, influenced_verts, obj)

        # 调整并应用RGBA胸部骨骼的缩放、旋转、位置
        apply_scale_diff(rb_parent_l, rb_parent_r, x_r, z_r, rb_scale_factor)
        apply_rotation_diff(
            root_l, armature_l, bone_l, dummy_head_lo_l, dummy_tail_lo_l, joint_parent_l,
            root_r, armature_r, bone_r, dummy_head_lo_r, dummy_tail_lo_r, joint_parent_r
        )
        apply_location_diff(root_l, armature_l, bone_l, dummy_tail_lo_l, root_r, armature_r, bone_r, dummy_tail_lo_r,
                            rb_parent_l)
        # 删除源模型胸部骨骼及对应的刚体Joint，防止刚体Joint重名
        b_names_l, b_names_r = remove_breast_bones(root, armature, rb_parent, kept_joints)

        # 通过MMD Tools手术，合并模型
        join_model(armature, armature_l, armature_r)

        # 重新获取源模型，即合并后的模型
        armature, objs, _, rb_parent = get_mmd_info(root)
        obj = objs[0]

        # 将胸部权重从源模型转移到左胸和右胸上
        for name_l in b_names_l:
            trans_vg(obj, name_l, BREAST_BL_NAME_L)
        for name_r in b_names_r:
            trans_vg(obj, name_r, BREAST_BL_NAME_R)

        # 修复胸饰与胸之间的父子关系与Joint连接
        repair_accessory(root, accessory_breast_rel_map, kept_joints)
        # 将胸部刚体绑定到源模型的身体骨骼
        bind_rb_to_body(rb_parent)
        # 设置胸部刚体碰撞组并对胸部刚体及胸部Joint重排序
        set_collision_and_resort(root, accessory_breast_rel_map, collision)
        # 恢复“物理”显示枠位置
        if physics_frame_index != -1:
            frames.move(frames.find(PHYSICAL_FRAME_NAME), physics_frame_index)
        else:
            frames.move(frames.find(PHYSICAL_FRAME_NAME), len(root.mmd_root.display_item_frames) - 1)

        # 汝窑百分比
        # 想保证开启物理前后胸部不上翘或下坠，始终保持原来的位置，Joint中右胸_後2、右胸_後2、右胸_前2、左胸_前2的值就不能变更
        # 想要控制汝窑程度 需要变更Joint中右胸_後2、右胸_後2、右胸_前2、左胸_前2的值
        # 可以通过修改权重的方式替代
        trans_vg(obj, BREAST_BL_NAME_L, UPPER_BODY2_NAME, factor=1 - factor, remove_source=False)
        trans_vg(obj, BREAST_BL_NAME_R, UPPER_BODY2_NAME, factor=1 - factor, remove_source=False)

        # 导出模型
        deselect_all_objects()
        select_and_activate(root)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        new_filepath = os.path.join(file_dir,
                                    f"{name}_RGBA_{format_factor(factor)}_{format_factor(rb_scale_factor)}_{COLLISION_MAP.get(props.collision)}_{timestamp}.pmx")

        export_pmx(new_filepath)

        # 删除临时集合内所有物体
        clean_tmp_collection()

        return name, "INFO", f"执行完成，模型文件地址：{new_filepath}"


def get_rb_bone_rel_map(rb_parent):
    rb_bone_map = {}
    rbn_rb_map = {}
    bone_rbs_map = defaultdict(list)

    for rb in rb_parent.children:
        name = rb.name
        rbn_rb_map[name] = rb

        bone = rb.mmd_rigid.bone
        if bone:
            rb_bone_map[name] = bone
            bone_rbs_map[bone].append(rb)

    return rb_bone_map, rbn_rb_map, bone_rbs_map


def set_collision_and_resort(root, accessory_breast_rel_map, collision):
    """
    设置刚体碰撞组并对RGBA胸部刚体及Joint重排序
    创建“双臂衝突刚体”，仅对“胸部刚体”碰撞，“胸部刚体”仅对“双臂衝突刚体”碰撞
    如果其它物理刚体的碰撞组与“胸部刚体”相同，且碰巧与“双臂衝突刚体”发生碰撞，也无所谓；如果其它物理刚体的碰撞组与“双臂衝突刚体”相同，且碰巧与“胸部刚体”发生碰撞，有影响概率较低
    创建“胸部衝突刚体”，仅对物理刚体碰撞，物理刚体是否对“胸部衝突刚体”碰撞，取决于原本设置，为了尽可能减少对碰撞组的占用，“胸部衝突刚体”的碰撞组与“胸部刚体”相同
    胸部首个子骨对应的刚体如果为“物理+骨骼”类型，则改为追踪骨骼，且不与胸部碰撞，如乱破
    胸部子孙骨和胸部如果有碰撞且穿模，设置为非碰撞，如朱鸢
    """
    armature, objs, joint_parent, rb_parent = get_mmd_info(root)
    rb_bone_map, rbn_rb_map, bone_rbs_map = get_rb_bone_rel_map(rb_parent)
    rigid_bodies = rb_parent.children
    physical_bone_names = get_physical_bone(root)
    physical_bone_names = set(physical_bone_names)
    bones = armature.data.bones

    # 不论碰撞策略如何设置，第一步均先将胸部物理碰撞关闭
    for rb in rigid_bodies:
        if rb.mmd_rigid.name_j not in RGBA_RB_NAMES:
            continue
        for i in range(16):
            rb.mmd_rigid.collision_group_mask[i] = True

    # 创建“双臂衝突刚体”，仅对“胸部刚体”碰撞
    limb_rb_map = {}
    if collision in ["DEFAULT"]:
        # 少前2 设置名称含“上半身”的刚体不与胸部碰撞，可能会影响其它“物理刚体”的碰撞，如头发，但几率较低
        for rb in rigid_bodies:
            if UPPER_BODY_NAME in rb.mmd_rigid.name_j:
                rb.mmd_rigid.collision_group_mask[BREAST_RB_GROUP] = True

        # 创建两臂衝突刚体，并设置两臂衝突刚体与胸部碰撞，且仅与胸部碰撞
        for rb in rigid_bodies:
            # 仅追踪骨骼类型
            if rb.mmd_rigid.type in ('1', '2'):
                continue
            # 避免重复创建
            name_j = rb.mmd_rigid.name_j
            if name_j in limb_rb_map:
                continue
            # 仅创建两臂衝突刚体
            if name_j not in LIMB_RB_NAMES:
                continue
            crb = copy_rb(rb)
            limb_rb_map[name_j] = crb
            crb_name = f"{name_j}衝突"
            crb.mmd_rigid.name_j = crb_name
            crb.name = f"AAA_{crb_name}"
            crb.mmd_rigid.collision_group_number = LIMB_RB_GROUP
            # 仅与胸部碰撞
            for i in range(16):
                if i == BREAST_RB_GROUP:
                    crb.mmd_rigid.collision_group_mask[i] = False
                else:
                    crb.mmd_rigid.collision_group_mask[i] = True

        # “胸部刚体”仅对“双臂衝突刚体”碰撞
        for rb in rigid_bodies:
            if rb.mmd_rigid.name_j in [BREAST_JP_NAME_L, BREAST_JP_NAME_R]:
                rb.mmd_rigid.collision_group_mask[LIMB_RB_GROUP] = False

        # 获取物理刚体所在碰撞组并去重
        # 物理刚体是否对“胸部衝突刚体”碰撞，取决于原本设置，而非全部设置为碰撞以防止冲突。案例如翡翠
        # 为了尽可能减少对碰撞组的占用，“胸部衝突刚体”的碰撞组与“胸部刚体”相同
        cgn_set = set()
        for rb in rigid_bodies:
            # 0:骨骼 1:物理 2:物理+骨骼
            if rb.mmd_rigid.type not in ('1', '2'):
                continue
            cgn = rb.mmd_rigid.collision_group_number
            if cgn == BREAST_RB_GROUP:
                continue
            cgn_set.add(cgn)

        # 创建胸部碰撞刚体
        breast_collision_rb_map = {}
        for rb in rigid_bodies:
            # 仅追踪骨骼类型
            if rb.mmd_rigid.type not in ('1', '2'):
                continue
            # 避免重复创建
            name_j = rb.mmd_rigid.name_j
            if name_j in breast_collision_rb_map:
                continue
            # 仅创建两臂衝突刚体
            if name_j not in [BREAST_JP_NAME_L, BREAST_JP_NAME_R]:
                continue
            crb = copy_rb(rb)
            breast_collision_rb_map[name_j] = crb
            crb_name = f"{name_j}衝突"
            crb.mmd_rigid.name_j = crb_name
            crb.name = f"AAA_{crb_name}"
            crb.mmd_rigid.type = "0"
            crb.mmd_rigid.collision_group_number = BREAST_RB_GROUP
            # 与物理部位碰撞
            for i in cgn_set:
                crb.mmd_rigid.collision_group_mask[i] = False

        # 胸部首个子骨对应的刚体如果为“物理+骨骼”类型，则改为追踪骨骼，如乱破
        for rb in rigid_bodies:
            if rb.mmd_rigid.type != '2':  # 限定 物理+骨骼 类型
                continue
            if rb.mmd_rigid.bone not in accessory_breast_rel_map:
                continue
            rb.mmd_rigid.type = '0'
            rb.mmd_rigid.collision_group_mask[BREAST_RB_GROUP] = True

        # 胸部子级和胸部如果有碰撞且穿模，设置为非碰撞，如朱鸢
        rgba_rbs = [rb for rb in rigid_bodies if rb.mmd_rigid.name_j in RGBA_RB_NAMES]
        accessory_bone_names = expand_accessory_bone_names(armature, accessory_breast_rel_map)
        for rb in rigid_bodies:
            if rb.mmd_rigid.type not in ('1', '2'):
                continue
            if rb.mmd_rigid.name_j in RGBA_RB_NAMES:
                continue
            if rb.mmd_rigid.bone not in accessory_bone_names:
                continue
            if rb.mmd_rigid.collision_group_mask[BREAST_RB_GROUP] is True:
                continue
            for rgba_rb in rgba_rbs:
                intersection = check_bvh_intersection(rb, rgba_rb)
                if intersection:
                    rb.mmd_rigid.collision_group_mask[BREAST_RB_GROUP] = True
                    break

    # 刚体顺序重排序
    breast_rbs = []
    for rb in rigid_bodies:
        name_j = rb.mmd_rigid.name_j
        if name_j in RGBA_RB2_NAMES:
            breast_rbs.append(rb)
    breast_rbs.sort(key=lambda rb: RGBA_RB2_NAMES.index(rb.mmd_rigid.name_j))
    limb_rbs = sorted(
        limb_rb_map.values(),
        key=lambda lrb: LIMB_RB_NAMES.index(lrb.mmd_rigid.name_j) if lrb.mmd_rigid.name_j in LIMB_RB_NAMES else -1
    )
    # ZZZ (36进制) = 46655（十进制）
    for index, rb in enumerate(limb_rbs + breast_rbs):
        set_index(rb, 10000 + index)

    # Joint重排序
    breast_joints = []
    for joint in joint_parent.children:
        name_j = joint.mmd_joint.name_j
        if name_j in RGBA_JOINT_NAMES:
            breast_joints.append(joint)
    breast_joints.sort(key=lambda j: RGBA_JOINT_NAMES.index(j.mmd_joint.name_j))
    for index, rb in enumerate(breast_joints):
        set_index(rb, 10000 + index)


def apply_location_diff(root_l, armature_l, bone_l, dummy_tail_lo_l, root_r, armature_r, bone_r, dummy_tail_lo_r,
                        rb_parent_l):
    """计算RGBA胸部骨骼tail与伪胸部骨骼tail的位置差，调整RGBA胸部骨骼tail使其位置与伪胸部骨骼tail一致"""
    # 获取胸骨tail和伪胸部tail的世界位置差值
    offset_l = dummy_tail_lo_l - armature_l.matrix_world @ bone_l.tail
    offset_r = dummy_tail_lo_r - armature_r.matrix_world @ bone_r.tail

    # 移动胸部root以适配源模型
    root_l.location += offset_l
    root_r.location += offset_r

    # 获取 胸部刚体前端 与 源模型胸部区域y最小值 的差值
    b_rb_l = next(r for r in rb_parent_l.children if r.mmd_rigid.name_j == BREAST_JP_NAME_L)
    world_cos = [b_rb_l.matrix_world @ v.co for v in b_rb_l.data.vertices]
    y_values = [co.y for co in world_cos]
    y_min = min(y_values)
    offset_y = (armature_r.matrix_world @ bone_r.tail).y - y_min

    # 移动胸部root以适配源模型
    root_l.location.y += offset_y
    root_r.location.y += offset_y

    # 应用位置
    apply_transform_to_objects([root_l, root_r], (True, False, False))


def apply_rotation_diff(root_l, armature_l, bone_l, dummy_head_lo_l, dummy_tail_lo_l, joint_parent_l,
                        root_r, armature_r, bone_r, dummy_head_lo_r, dummy_tail_lo_r, joint_parent_r):
    """计算RGBA胸部骨骼与伪胸部骨骼的旋转差，调整RGBA胸部骨骼使其旋转与伪胸部骨骼一致（仅在水平面上进行旋转）"""
    # 获取胸部骨骼实际方向
    direction_l = (armature_l.matrix_world @ bone_l.head - armature_l.matrix_world @ bone_l.tail).normalized()
    direction_r = (armature_r.matrix_world @ bone_r.head - armature_r.matrix_world @ bone_r.tail).normalized()

    # 计算伪胸部骨骼方向向量
    dummy_direction_l = (dummy_head_lo_l - dummy_tail_lo_l).normalized()
    dummy_direction_r = (dummy_head_lo_r - dummy_tail_lo_r).normalized()

    # 计算旋转差（四元数→欧拉）
    rot_diff_quat_l = direction_l.rotation_difference(dummy_direction_l)
    rot_diff_quat_r = direction_r.rotation_difference(dummy_direction_r)
    rot_diff_euler_l = rot_diff_quat_l.to_euler('XYZ')
    rot_diff_euler_r = rot_diff_quat_r.to_euler('XYZ')

    # 旋转胸部root以适配源模型（仅在水平面上进行旋转）
    root_l.rotation_mode = 'XYZ'
    root_r.rotation_mode = 'XYZ'
    root_l.rotation_euler.z += rot_diff_euler_l.z
    root_r.rotation_euler.z += rot_diff_euler_r.z

    # 应用旋转
    apply_transform_to_objects([root_l, root_r], (False, True, False))

    # 应用Joint缩放，防止未归1的缩放导致模型在导出时，Joint属性值发生变化
    apply_transform_to_objects([joint_parent_l, joint_parent_r], (False, False, True))
    apply_transform_to_objects(joint_parent_l.children + joint_parent_r.children, (False, False, True))


def apply_scale_diff(rb_parent_l, rb_parent_r, x_r, z_r, rb_scale_factor):
    """计算RGBA胸部骨骼与伪胸部骨骼的缩放差，调整RGBA胸部骨骼使其缩放与伪胸部骨骼一致"""
    # 刚体缩放 如果碰撞了 就缩小点或取消碰撞 如果缺少碰撞就调大些
    # 计算RGBA胸部刚体半径和一侧胸部最长线段（一半、平均）的缩放差
    b_rb_l = next(r for r in rb_parent_l.children if r.mmd_rigid.name_j == BREAST_JP_NAME_L)
    b_rb_r = next(r for r in rb_parent_r.children if r.mmd_rigid.name_j == BREAST_JP_NAME_R)
    breast_r = (x_r + z_r) / 2
    r = b_rb_l.mmd_rigid.size[0]
    scale_factor = breast_r / r * rb_scale_factor

    # 缩放RGBA胸部刚体以适配源模型胸部区域
    b_rb_l.mmd_rigid.size[0] *= scale_factor
    b_rb_r.mmd_rigid.size[0] *= scale_factor


def get_dummy_breast(armature, breast_bones, horizontal_bones, influenced_verts, obj):
    """获取伪胸部骨骼的坐标"""
    world_cos = [obj.matrix_world @ v.co for v in influenced_verts]
    x_values = [co.x for co in world_cos]
    y_values = [co.y for co in world_cos]
    z_values = [co.z for co in world_cos]

    # 伪胸部骨骼的 tail.y 取自 influenced_verts 中 y 值最小的顶点
    y_min = min(y_values)
    # 伪胸部骨骼的 tail.x 取自 influenced_verts 中 一侧x 值最大的顶点 与 0 的均值
    x_r = x_max = max(x_values)
    avg_x = x_max / 2
    # 伪胸部骨骼的 tail.z 取自 influenced_verts 中 z 值最大最小两点的均值
    z_min, z_max = min(z_values), max(z_values)
    avg_z = (z_min + z_max) / 2
    z_r = (z_max - z_min) / 2

    dummy_tail_lo_l = mathutils.Vector((abs(avg_x), y_min, avg_z))
    dummy_tail_lo_r = mathutils.Vector((-abs(avg_x), y_min, avg_z))

    # 从水平胸部骨骼中，获取head坐标中y值最大的骨骼，并计算伪胸部骨骼head位置
    if horizontal_bones:
        max_head_bone = max(horizontal_bones, key=lambda b: (armature.matrix_world @ b.head_local).y)
    else:
        max_head_bone = max(breast_bones, key=lambda b: (armature.matrix_world @ b.head_local).y)
    max_head_co = armature.matrix_world @ max_head_bone.head_local
    dummy_head_lo_l = mathutils.Vector((abs(max_head_co.x), max_head_co.y, dummy_tail_lo_l.z))
    dummy_head_lo_r = mathutils.Vector((-abs(max_head_co.x), max_head_co.y, dummy_tail_lo_r.z))
    return dummy_head_lo_l, dummy_head_lo_r, dummy_tail_lo_l, dummy_tail_lo_r, x_r, z_r


def remove_breast_bones(root, armature, rb_parent, kept_joints):
    # 记录被删除的骨骼属于左侧还是右侧
    breast_bones = get_breast_bones(root)
    breast_names = [b.name for b in breast_bones]
    b_names_l = []
    b_names_r = []

    deselect_all_objects()
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature.data.edit_bones
    for bone in breast_bones:
        eb = edit_bones.get(bone.name)
        if not eb:
            continue
        # 检查 bone.head 和 bone.tail 的 x 坐标来确定左右
        if eb.tail.x > 0:
            b_names_l.append(bone.name)
        elif eb.tail.x < 0:
            b_names_r.append(bone.name)
    bpy.ops.object.mode_set(mode='OBJECT')

    # 记录刚体与其关联骨骼的map
    _, _, bone_rbs_map = get_rb_bone_rel_map(rb_parent)

    # 少数特殊模型（例如二重螺旋的赛琪）中，即使物理刚体未直接关联骨骼，也可能通过Joint与其他刚体产生关联，因此这种情况是正常的。
    # 为了避免误删，这里通过记录“被删除的骨骼其关联的刚体有哪些”来实现“删除骨骼时同时删除其对应刚体”的目的，而不是简单地将“未关联骨骼的刚体”全部删除。
    rbs_to_remove = []
    # 删除冗余骨骼
    deselect_all_objects()
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature.data.edit_bones
    to_delete = [eb.name for eb in edit_bones if eb.name in breast_names]
    for name in to_delete:
        rbs_to_remove.extend(bone_rbs_map.get(name, []))
        edit_bones.remove(edit_bones[name])
    bpy.ops.object.mode_set(mode='OBJECT')

    # 清理无效刚体Joint
    remove_invalid_rigidbody_joint(root, rbs_to_remove, kept_joints)
    return b_names_l, b_names_r


def join_model(armature, armature_l, armature_r):
    deselect_all_objects()
    select_and_activate(armature_l)
    select_and_activate(armature_r)
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='POSE')
    # 取消选中源模型所有骨骼
    for pb in armature.pose.bones:
        pb.bone.select = False
    # 选中左右胸部骨骼，选中源模型“上半身2”骨骼
    for pb in armature_l.pose.bones:
        pb.bone.select = True
    for pb in armature_r.pose.bones:
        pb.bone.select = True
    for pb in armature.pose.bones:
        if pb.name == UPPER_BODY2_NAME:
            pb.bone.select = True
            armature.data.bones.active = pb.bone
    # 合并模型
    bpy.ops.mmd_tools.model_join_by_bones()


def bind_rb_to_body(rb_parent):
    """
    将胸部刚体绑定到源模型的身体骨骼。
    由于无法确定源模型是否存在“上半身2”刚体，因此对“上半身2.L”和“上半身2.R”进行冗余处理，并调整其碰撞组和尺寸。
    """
    for rb in rb_parent.children:
        if rb.mmd_rigid.name_j in ["上半身2_L", "上半身2_R"]:
            rb.mmd_rigid.bone = UPPER_BODY2_NAME
            for i in range(16):
                rb.mmd_rigid.collision_group_mask[i] = True
            rb.mmd_rigid.size[0] = 0.01
            rb.mmd_rigid.size[1] = 0.01


def repair_accessory(root, accessory_breast_rel_map, kept_joints):
    """修复胸饰与胸之间的父子关系与Joint连接"""
    armature, _, joint_parent, rb_parent = get_mmd_info(root)

    # 重新连接骨骼父子级
    deselect_all_objects()
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='EDIT')
    for bbc_name, bb_name in accessory_breast_rel_map.items():
        if ".L" in bb_name:
            armature.data.edit_bones.get(bbc_name).parent = armature.data.edit_bones.get(BREAST_BL_NAME_L)
        else:
            armature.data.edit_bones.get(bbc_name).parent = armature.data.edit_bones.get(BREAST_BL_NAME_R)
    bpy.ops.object.mode_set(mode='OBJECT')

    # 重新连接无效关节
    b_rb_l = next(r for r in rb_parent.children if r.mmd_rigid.name_j == BREAST_JP_NAME_L)
    b_rb_r = next(r for r in rb_parent.children if r.mmd_rigid.name_j == BREAST_JP_NAME_R)

    for joint in reversed(joint_parent.children):
        side = kept_joints.get(joint.name)
        if not side:
            continue
        target_rb = b_rb_l if side == "L" else b_rb_r
        rbc = joint.rigid_body_constraint

        if not rbc.object1:
            rbc.object1 = target_rb
        if not rbc.object2:
            rbc.object2 = target_rb


def get_accessory_info(armature, breast_bones, breast_names, joint_parent, rb_parent):
    """
    获取胸部饰品信息，用于后续处理：
        1. 修复骨骼的父子关系
        2. 修复Joint的连接关系
    """
    # 胸饰品指胸骨的子骨骼，例如胸飾、胸坠、胸結等（bbc 即 breast_bone_child）
    # 记录胸饰品根骨骼和胸部骨骼的关系，供后续修复骨骼父子级用
    accessory_breast_rel_map = {}
    for bb in breast_bones:
        for bbc in bb.children:
            if bbc.name not in breast_names and not is_dummy_bone(bbc.name):
                accessory_breast_rel_map[bbc.name] = bb.name

    # 获取胸部刚体名称列表与胸饰品刚体名称列表
    breast_rb_names = [rb.name for rb in rb_parent.children if rb.mmd_rigid.bone in breast_names]
    accessory_bone_names = expand_accessory_bone_names(armature, accessory_breast_rel_map)
    accessory_rb_names = [rb.name for rb in rb_parent.children if rb.mmd_rigid.bone in accessory_bone_names]

    # 记录链接胸和胸饰品的Joint，避免后续被删除，供后续修复Joint连接用
    kept_joints = {}
    for joint in joint_parent.children:
        object1 = joint.rigid_body_constraint.object1
        object2 = joint.rigid_body_constraint.object2

        # todo 根据实际位置确定左右
        if object1.name in accessory_rb_names and object2.name in breast_rb_names:
            kept_joints[joint.name] = "L" if "左" in object2.name else "R"
        elif object1.name in breast_rb_names and object2.name in accessory_rb_names:
            kept_joints[joint.name] = "L" if "左" in object1.name else "R"
    return accessory_breast_rel_map, kept_joints


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


def get_physical_bone(root):
    """获取受物理影响的骨骼"""
    rigidbody_parent = find_rigid_body_parent(root)
    if rigidbody_parent is None:
        return []
    rigid_bodies = rigidbody_parent.children
    # 受刚体物理影响的骨骼名称列表（blender名称）
    bl_names = []
    for rigidbody in rigid_bodies:
        # 存在有刚体但没有关联骨骼的情况
        if rigidbody.mmd_rigid.bone == '':
            continue
        # 0:骨骼 1:物理 2:物理+骨骼
        if rigidbody.mmd_rigid.type not in ('1', '2'):
            continue
        bl_names.append(rigidbody.mmd_rigid.bone)
    return bl_names


def clean_tmp_collection():
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


def apply_transform_to_objects(objects, trans):
    """对一组对象应用变换（仅缩放）"""
    for obj in objects:
        show_object(obj)
        deselect_all_objects()
        select_and_activate(obj)
        bpy.ops.object.transform_apply(location=trans[0], rotation=trans[1], scale=trans[2])


def trans_vg(obj, source_vg_name, target_vg_name, factor=1.0, remove_source=True):
    """
    将顶点权重从源顶点组传递到目标顶点组。

    参数:
    obj: Blender 对象
    source_vg_name: 源顶点组名称
    target_vg_name: 目标顶点组名称
    factor: 权重传递比例 (0~1)，默认 1.0
    remove_source: 是否从源顶点组移除对应权重（True: 完全转移, False: 按 factor 减少源权重）
    """
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


def filter_horizontal_bones(armature, breast_bones):
    # 假设 breast_bones 已经存在
    # 夹角阈值 30 度
    angle_threshold = math.radians(30)

    filtered_bones = []

    for b in breast_bones:
        # 骨骼在世界空间的 head 和 tail
        head_world = armature.matrix_world @ b.head_local
        tail_world = armature.matrix_world @ b.tail_local

        # 计算骨骼向量
        bone_vec = tail_world - head_world

        # 投影到 XY 平面
        bone_vec_xy = mathutils.Vector((bone_vec.x, bone_vec.y, 0))

        # 如果骨骼长度很小，跳过避免除零
        if bone_vec.length == 0 or bone_vec_xy.length == 0:
            continue

        # 夹角 = 骨骼向量与水平投影向量的夹角
        angle = bone_vec.angle(bone_vec_xy)

        if angle < angle_threshold:
            filtered_bones.append(b)

    return filtered_bones


def get_vertices_influenced_by_bones(obj, bone_names):
    verts = []
    mesh = obj.data

    for v in mesh.vertices:
        total_weight = 0.0
        for g in v.groups:
            vg_name = obj.vertex_groups[g.group].name
            if vg_name in bone_names:
                total_weight += g.weight

        if total_weight > WEIGHT_THRESHOLD:
            verts.append(v)

    return verts


def remove_invalid_rigidbody_joint(root, rbs_to_remove, kept_joints):
    """清理无效刚体Joint"""
    original_mode = bpy.context.active_object.mode
    armature, objs, joint_parent, rb_parent = get_mmd_info(root)

    # （预先）删除无效关节
    for joint in reversed(joint_parent.children):
        if joint.name in kept_joints.keys():
            continue
        rigidbody1 = joint.rigid_body_constraint.object1
        rigidbody2 = joint.rigid_body_constraint.object2
        if any(r not in rb_parent.children for r in [rigidbody1, rigidbody2]):
            bpy.data.objects.remove(joint, do_unlink=True)

    # 处理刚体
    for rigidbody in reversed(rb_parent.children):
        # 虽然这些刚体在删除对应骨骼时会一并被删除，但它们有可能被错误地绑定到非胸部骨骼上，所以在此强制删除
        name_j = rigidbody.mmd_rigid.name_j
        if name_j in RGBA_RB_NAMES:
            bpy.data.objects.remove(rigidbody, do_unlink=True)
            continue
        match = BREAST_RB_PATTERN.match(name_j)
        if match and rigidbody.mmd_rigid.type in ('1', '2'):
            bpy.data.objects.remove(rigidbody, do_unlink=True)
            continue
        match = check_girlsfrontline_breast_bones_and_rbs(name_j)
        if match and rigidbody.mmd_rigid.type in ('1', '2'):
            bpy.data.objects.remove(rigidbody, do_unlink=True)
            continue

        # 关联骨骼不存在则删除这个刚体
        if rigidbody.name in rbs_to_remove:
            bpy.data.objects.remove(rigidbody, do_unlink=True)
            continue
        # 当刚体没有关联骨骼时，刚体可能会被Joint关联，所以不会导致问题，因此这种情况不处理，取决于模型本身

    # 删除无效关节
    for joint in reversed(joint_parent.children):
        if joint.name in kept_joints.keys():
            continue
        rigidbody1 = joint.rigid_body_constraint.object1
        rigidbody2 = joint.rigid_body_constraint.object2
        if not rigidbody1 or not rigidbody2:
            bpy.data.objects.remove(joint, do_unlink=True)

    bpy.ops.object.mode_set(mode=original_mode)


def format_factor(factor, decimals=2):
    return f"{factor:.{decimals}f}".rstrip('0').rstrip('.')


def set_index(obj, index):
    m = RB_JOINT_PREFIX_REGEXP.match(obj.name)
    name = m.group('name') if m else obj.name
    obj.name = '%s_%s' % (int2base(index, 36, 3), name)


def expand_accessory_bone_names(armature, accessory_breast_rel_map):
    """根据胸饰骨骼关系表递归扩展所有子骨骼名称"""
    bones = armature.data.bones
    accessory_bone_names = list(accessory_breast_rel_map.keys())

    def recurse(target_name, collected):
        bone = bones.get(target_name)
        if not bone:
            return
        for child in bone.children:
            if child.name not in collected:
                collected.append(child.name)
                recurse(child.name, collected)

    # 对每个起始骨骼递归扩展
    for root_name in list(accessory_bone_names):
        recurse(root_name, accessory_bone_names)

    return accessory_bone_names


def recursive_search(props):
    """寻找指定路径下各个子目录中，时间最新且未进行处理的那个模型"""
    batch = props.batch
    directory = batch.directory
    threshold = batch.threshold
    conflict_strategy = batch.conflict_strategy

    file_list = []
    pmx_count = 0
    for root, dirs, files in os.walk(directory):
        flag = False

        for file in files:
            if file.endswith('.pmx') or file.endswith('.pmd'):
                flag = True
                pmx_count += 1
        if flag:
            curr_list = []  # 当前目录下符合条件的文件
            model_files = [f for f in files
                           if (f.endswith('.pmx') or f.endswith('.pmd'))
                           and os.path.getsize(os.path.join(root, f)) > threshold * 1024]  # 排除掉已被排除的文件的影响

            # 如果满足条件的model_files有多个，获取全部
            for model_file in model_files:
                curr_list.append(model_file)

            pattern = re.compile(
                r'^(.+)_RGBA_(\d+(?:\.\d+)?)_(\d+(?:\.\d+)?)_(默认|无碰撞)_(\d{14})$'
            )
            files_to_remove = []
            for file in reversed(curr_list):
                match = pattern.match(os.path.splitext(file)[0])
                if not match:
                    continue
                name, _, _, _, _ = match.groups()
                new_filename_key = (f"{name}_RGBA_"
                                    f"{format_factor(round_to_two_decimals(props.factor))}_"
                                    f"{format_factor(round_to_two_decimals(props.rb_scale_factor))}_"
                                    f"{COLLISION_MAP.get(props.collision)}")
                files_to_remove.append(file)
                if new_filename_key in file:
                    if conflict_strategy == 'SKIP':
                        source_file = os.path.join(root, f"{name}.pmx")
                        if os.path.exists(source_file):
                            files_to_remove.append(f"{name}.pmx")
                    else:
                        pass

            for file in reversed(files_to_remove):
                if file in curr_list:
                    curr_list.remove(file)

            for file in curr_list:
                file_list.append(os.path.join(root, file))
    msg = bpy.app.translations.pgettext_iface("Actual files to process: {}. Total files: {}, skipped: {}").format(
        len(file_list), pmx_count, pmx_count - len(file_list)
    )
    print(msg)
    return file_list


def check_batch_props(operator, batch):
    directory = batch.directory

    # 获取目录的全限定路径 这里用blender提供的方法获取，而不是os.path.abspath。没有必要将相对路径转为绝对路径，因为哪种路径是由用户决定的
    # https://blender.stackexchange.com/questions/217574/how-do-i-display-the-absolute-file-or-directory-path-in-the-ui
    # 如果用户随意填写，可能会解析成当前blender文件的同级路径，但不影响什么
    abs_path = bpy.path.abspath(directory)
    if not os.path.exists(abs_path):
        operator.report(type={'ERROR'}, message=f'Model directory not found!')
        return False
    # 获取目录所在盘符的根路径
    drive, tail = os.path.splitdrive(abs_path)
    drive_root = os.path.join(drive, os.sep)
    # 校验目录是否是盘符根目录
    if abs_path == drive_root:
        operator.report(type={'ERROR'}, message=f'Invalid root directory! Change to subfolder.')
        return False

    return True


def round_to_two_decimals(value):
    """将数值四舍五入到小数点后两位"""
    return round(value, 2)


def create_bvh_tree_from_object(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    return bvh


def check_bvh_intersection(obj_1, obj_2):
    """https://blender.stackexchange.com/questions/9073/how-to-check-if-two-meshes-intersect-in-python"""
    bvh1 = create_bvh_tree_from_object(obj_1)
    bvh2 = create_bvh_tree_from_object(obj_2)
    return bvh1.overlap(bvh2)
