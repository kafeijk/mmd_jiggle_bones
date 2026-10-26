import os
import math
import re
import time
import traceback
from collections import defaultdict, OrderedDict
from datetime import datetime

import bpy
import mathutils
from mathutils.bvhtree import BVHTree

from .analysis_operators import AnalysisStatus
from .analysis_operators import batch_do_analysis
from .analysis_operators import build_all_failed_msg
from .analysis_operators import filter_results
from .analysis_operators import preprocess
from .analysis_operators import process_source_objs
from .analysis_operators import validate_analysis_param
from ..mmd_utils import FnModel
from ..constants import (
    ADDON_ID_PREFIX,
    BREAST_BL_NAME_L, BREAST_BL_NAME_R, BREAST_JP_NAME_L, BREAST_JP_NAME_R,
    BREAST_RB_PATTERN, COLLISION_SUFFIX, CUP_LIST, INVALID_CHARS, LIMB_RB_GROUP,
    LIMB_RB_NAMES, MODEL_SCALE, NECK_NAME, ORDER_PREFIX_BASE, ORDER_PREFIX_START,
    ORDER_PREFIX_WIDTH, PHYSICAL_FRAME_NAME, RB_JOINT_PREFIX_REGEXP,
    RGBA_JOINT_NAMES, RGBA_RB2_NAMES, RGBA_RB_NAMES, SENSITIVITY_RB_NAMES,
    TASK_SET_RGBA, TIMER_INTERVAL, TMP_ANALYSIS_COLLECTION_NAME,
    TMP_COLLECTION_NAME, UPPER_BODY_NAME,
)
from ..utils import (
    adjust_ang_limits, bmesh_from_mesh, check_girlsfrontline_breast_bones_and_rbs,
    clean_temp_data, deselect_all_objects, export_pmx, find_joint_parent,
    find_pmx_armature, find_pmx_objects, find_rigid_body, find_rigid_body_parent,
    get_breast_bones_map, get_collection, get_vertices_influenced_by_bones,
    import_pmx, int2base, is_dummy_bone, is_mmd_tools_enabled,
    remap_breast_weights, restore_selection, select_and_activate, set_batch_progress,
    show_object, snapshot_data_blocks, tag_redraw, trans_vg,
)


def get_mmd_info(root):
    armature = find_pmx_armature(root)
    objs = find_pmx_objects(armature)
    joint_parent = find_joint_parent(root)
    rb_parent = find_rigid_body_parent(root)
    return armature, objs, joint_parent, rb_parent


def format_failed_files(directory, name_msg_map):
    """生成失败文件报告：默认显示文件名，重名时显示相对路径"""
    base_names = defaultdict(list)
    for filepath in name_msg_map:
        base_names[os.path.splitext(os.path.basename(filepath))[0]].append(filepath)

    duplicated = {base for base, paths in base_names.items() if len(paths) > 1}

    lines = []
    for filepath, msg in name_msg_map.items():
        base = os.path.splitext(os.path.basename(filepath))[0]
        label = os.path.splitext(os.path.relpath(filepath, directory))[0] if base in duplicated else base
        lines.append(f"{label} - {msg}")
    return "\n".join(lines)


def copy_rb(rb):
    new_mesh = rb.data.copy()
    new_rb = rb.copy()
    new_rb.data = new_mesh

    # 复制带刚体的对象时，Blender 会自动把副本加入刚体世界集合；
    # 因此不能直接链接到 rb.users_collection[0]（可能正好是刚体世界集合，会报“已在集合里”）
    rigid_world_collections = {
        scene.rigidbody_world.collection
        for scene in bpy.data.scenes
        if scene.rigidbody_world is not None
    }
    target_col = next(
        (col for col in rb.users_collection if col not in rigid_world_collections), None)
    if target_col is not None and new_rb.name not in target_col.objects:
        target_col.objects.link(new_rb)
    return new_rb


def set_sensitivity(rb_parent, props):
    """
    设置灵敏度，用于解决部分动作幅度较小时，胸部摇晃不明显的问题。
    注意：灵敏度实际通过调整刚体质量实现，会导致胸部受重力影响产生向下偏转。使用时应引导用户将角度限制X的正向限制设置为0，或提前告知该潜在影响。
    """
    mode = props.jiggle_adjustment_mode
    if mode in ["DEFAULT", "CUSTOM_SIMPLIFY"]:
        return
    for rb in rb_parent.children:
        name = rb.mmd_rigid.name_j
        if name not in SENSITIVITY_RB_NAMES:
            continue
        rb.rigid_body.mass = props.sensitivity


class SetRgbaOperator(bpy.types.Operator):
    bl_idname = f"{ADDON_ID_PREFIX}.set_rgba"
    bl_label = "Execute"

    bl_description = (
        "RGBA式胸部物理移植\n"
        "生成的模型无法在Blender、MMM、NexGiMa中直接进行烘焙\n"
        "如需烘焙，请使用MMD桥生成带物理的VMD文件，或采用ABC流程\n"
        "批量操作不可撤销，请确认后再执行")
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        props = scene.mmd_jiggle_tools_set_rgba

        self.active_object = context.active_object
        self.selected_objects = bpy.context.selected_objects

        if not self.check_props(props):
            return {'CANCELLED'}

        self.file_list = recursive_search(props)
        if not self.file_list:
            self.report({'WARNING'}, "未找到需要处理的文件，或所有文件均已处理完毕")
            return {'CANCELLED'}

        self.start_time = time.time()
        self.file_index = 0
        self.name_msg_map = OrderedDict()
        self.processing_file = False  # 控制UI刷新与主流程交替执行
        # 记录批处理开始前的数据块，后续只清理本次新增的，避免误删用户数据
        self.data_snapshot = snapshot_data_blocks()
        # 进度条所在面板首次展开需要重新布局，先跳过两帧并主动重绘（见 modal）
        self.flush_count = 0

        # 初始化进度条，显示内容为第一个模型
        self.update_progress()

        # 后台（-b）没有事件循环，modal 不会被驱动，直接同步跑完所有文件
        if bpy.app.background:
            return self.run_sync(context, props)

        self.timer = context.window_manager.event_timer_add(TIMER_INTERVAL, window=context.window)
        context.window_manager.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def run_sync(self, context, props):
        """后台模式：同步处理完所有文件，供 -b 测试使用"""
        try:
            while self.file_index < len(self.file_list):
                self._process_current_file(props)
            return self._finish(context, props)
        except Exception as e:
            self._end(context)
            traceback.print_exc()
            self.report({'ERROR'}, f"批量处理发生异常：{e}")
            return {'CANCELLED'}

    def _end(self, context):
        """移除定时器并复位处理状态，保证任何结束路径都不会留下“处理中”状态"""
        if getattr(self, "timer", None) is not None:
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None

        set_batch_progress(TASK_SET_RGBA, False)
        tag_redraw(context)

    def modal(self, context, event):
        # 允许用户通过 ESC 结束批处理，避免流程无法退出
        if event.type == 'ESC':
            self._end(context)
            self.report({'INFO'}, "已取消处理")
            return {'CANCELLED'}

        # 忽略用户的操作，仅处理TIMER事件，
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}

        # 进度条所在面板首次展开需要重新布局：先跳过两帧并主动重绘，避免界面来不及画
        if self.flush_count < 20:
            self.flush_count += 1
            tag_redraw(context)
            return {'RUNNING_MODAL'}

        props = context.scene.mmd_jiggle_tools_set_rgba

        try:
            if self.file_index >= len(self.file_list):
                return self._finish(context, props)

            self._process_current_file(props)
        except Exception as e:
            # 任何未预期异常都结束流程并复位状态，避免modal静默中断后UI卡死
            self._end(context)
            self.report({'ERROR'}, f"批量处理发生异常：{e}")
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _finish(self, context, props):
        self._end(context)

        total_time = time.time() - self.start_time

        if self.name_msg_map:
            directory = bpy.path.abspath(props.batch.directory)
            combined_msg = format_failed_files(directory, self.name_msg_map)

            msg = (f"{len(self.file_list) - len(self.name_msg_map)}/{len(self.file_list)} 个文件已处理完成"
                   f"（总耗时 {total_time:.2f}s）。点击查看详细报告 ↑↑↑")

            self.report({'WARNING'}, combined_msg)
            self.report({'WARNING'}, msg)

        else:
            abs_path = bpy.path.abspath(props.batch.directory).rstrip("\\/")
            msg = f"目录“{abs_path}”处理完成（总耗时 {total_time:.2f}s）"
            self.report({'INFO'}, msg)
        return {'FINISHED'}

    def _process_current_file(self, props):
        # 主流程
        file_start = time.time()

        filepath = self.file_list[self.file_index]
        file_name = os.path.basename(filepath)

        try:
            # 以完整路径为 key，避免不同目录下的同名文件互相覆盖
            _, status, msg = self.set_rgba(props, f_path=filepath)
            if status == "ERROR":
                self.name_msg_map[filepath] = msg
        except Exception as e:
            # 单个文件处理失败不中断整批，记录错误后继续处理下一个文件
            self.name_msg_map[filepath] = f"处理异常：{e}"
            print(f'文件“{file_name}”处理异常：{e}')
        finally:
            self.file_index += 1
            # 恢复选中状态
            restore_selection(self.selected_objects, self.active_object)
            # 更新进度
            self.update_progress()

        elapsed_file = time.time() - file_start
        elapsed_total = time.time() - self.start_time

        print(
            f'文件“{file_name}”处理结束，'
            f'进度 {self.file_index}/{len(self.file_list)}'
            f'（当前耗时 {elapsed_file:.2f}s，'
            f'总耗时 {elapsed_total:.2f}s）'
        )

    def update_progress(self):
        if self.file_index < len(self.file_list):
            filepath = self.file_list[self.file_index]
            file_name = os.path.basename(filepath)

            current = self.file_index + 1
            total = len(self.file_list)
            if bpy.app.version >= (4, 0, 0):
                text = f"正在处理：{file_name}"
            else:
                text = f"{current}/{total} 正在处理：{file_name}"

            set_batch_progress(TASK_SET_RGBA, True, current / total, text, current, total)
            tag_redraw(bpy.context)

    def check_props(self, props):
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "MMD Tools plugin is not enabled!")
            return False

        batch = props.batch
        if not check_batch_props(self, batch):
            return False

        return True

    def set_rgba(self, props, f_path=None):
        """处理单个模型；无论成功、提前返回还是异常，都清理本次产生的临时数据"""
        try:
            return self._set_rgba_impl(props, f_path)
        except Exception:
            # 异常时先退出编辑/姿态模式，保证临时对象可以安全删除
            try:
                deselect_all_objects()
            except Exception:
                pass
            # 继续向上抛
            raise
        finally:
            try:
                clean_temp_data(
                    TMP_COLLECTION_NAME,
                    snapshot=getattr(self, "data_snapshot", None),
                    only_marked=False,
                )
            except Exception:
                traceback.print_exc()

    def _set_rgba_impl(self, props, f_path=None):
        mode = props.jiggle_adjustment_mode
        batch = props.batch
        rb_scale_factor = round_to_two_decimals(props.rb_scale_factor)
        filepath = f_path

        # 防止MMD Tools插件的导入Bug，这里需将当前帧调整为0或1
        bpy.context.scene.frame_current = 0
        # 获取临时集合，在临时集合中进行模型的处理
        get_collection(TMP_COLLECTION_NAME)

        # 获取源模型名称
        abs_path = bpy.path.abspath(filepath)
        file_dir = os.path.dirname(bpy.path.abspath(filepath))
        file_name = os.path.basename(abs_path)
        name, ext = os.path.splitext(file_name)

        # 导入源模型
        import_pmx(filepath)
        root = bpy.context.active_object
        armature, objs, joint_parent, rb_parent = get_mmd_info(root)
        if not objs:
            return name, "ERROR", "源模型中未找到网格"
        obj = objs[0]

        # 获取源模型胸部骨骼列表
        breast_bones_map = get_breast_bones_map(armature, props.extra_bones_str)
        if not breast_bones_map:
            return name, "ERROR", "源模型中未找到胸部骨骼"
        breast_bones = next(iter(breast_bones_map.values()))
        breast_names = [b.name for b in breast_bones]

        # 筛选源模型胸部骨骼中的水平胸部骨骼，用于计算位置
        horizontal_bones = filter_horizontal_bones(armature, breast_bones)
        # 从源模型胸部顶点中筛选权重大于WEIGHT_THRESHOLD的顶点，作为胸部网格范围，用于定位伪胸部骨骼的坐标
        primary, marginal = get_vertices_influenced_by_bones(obj, [b.name for b in breast_bones])
        if not primary and not marginal:
            return name, "ERROR", f"{AnalysisStatus.NO_BREAST_VERTS.msg}\n已识别骨骼名称参考：{breast_names}"
        influenced_verts = primary if primary else marginal

        # “首”骨骼及其父级用于确定胸部绑定的身体骨骼
        neck_bone = armature.data.bones.get(NECK_NAME)
        if neck_bone is None:
            return name, "ERROR", f"源模型中未找到名称为“{NECK_NAME}”的骨骼"
        if neck_bone.parent is None:
            return name, "ERROR", f"“{NECK_NAME}”骨骼缺少父级骨骼"
        target_upper_body_name = neck_bone.parent.name

        # 部分模型（如武器模型）可能仅包含骨骼，未定义刚体和关节。
        # 但部分角色类型模型也可能缺少刚体/关节配置，导致rb_parent或joint_parent为空，进而影响后续处理流程。
        # 对通过权重校验但缺少rb_parent或joint_parent的模型，按新版mmd_tools（bl_ext.blender_org.mmd_tools）的方式补充空物体。
        if not rb_parent or not joint_parent:
            if not rb_parent:
                FnModel.ensure_rigid_group_object(bpy.context, root)
            if not joint_parent:
                FnModel.ensure_joint_group_object(bpy.context, root)

            # 重新获取并显示
            _, _, joint_parent, rb_parent = get_mmd_info(root)
            show_object(joint_parent)
            show_object(rb_parent)

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
        bone_r = armature_r.pose.bones.get(BREAST_BL_NAME_R)

        # 获取伪胸部骨骼的坐标
        dummy_head_lo_l, dummy_head_lo_r, dummy_tail_lo_l, dummy_tail_lo_r, x_r, z_r = get_dummy_breast_coords(
            armature, breast_bones, horizontal_bones, influenced_verts, obj)

        # 调整并应用RGBA胸部骨骼的缩放、旋转、位置
        apply_scale_diff(rb_parent_l, rb_parent_r, x_r, z_r, rb_scale_factor)
        apply_rotation_diff(
            root_l, armature_l, bone_l, dummy_head_lo_l, dummy_tail_lo_l,
            root_r, armature_r, bone_r, dummy_head_lo_r, dummy_tail_lo_r)
        apply_location_diff(root_l, armature_l, bone_l, dummy_tail_lo_l,
                            root_r, armature_r, bone_r, dummy_tail_lo_r, rb_parent_l)
        # 删除源模型胸部骨骼及对应的刚体Joint，防止刚体Joint重名
        b_names_l, b_names_r = remove_breast_bones(root, armature, rb_parent, kept_joints, props.extra_bones_str)
        # 通过MMD Tools手术，合并模型
        join_model(armature, armature_l, armature_r, target_upper_body_name)

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
        bind_rb_to_body(rb_parent, target_upper_body_name)
        # 设置胸部刚体碰撞组、修改胸饰刚体质量、对胸部刚体及Joint重排序
        configure_collision_mass_order(root, accessory_breast_rel_map, props)
        # 恢复“物理”显示枠位置
        if physics_frame_index != -1:
            frames.move(frames.find(PHYSICAL_FRAME_NAME), physics_frame_index)
        else:
            # 物理显示枠来源于RGBA素材
            frames.move(frames.find(PHYSICAL_FRAME_NAME), len(root.mmd_root.display_item_frames) - 1)

        # 摇晃幅度为默认时，获取尺码信息
        result = None
        if mode == "DEFAULT":
            err_msg, result = analyze_breast(root, props.extra_bones_str)
            if err_msg:
                return name, "ERROR", err_msg

        # 重映射权重
        # 因为改变了权重分布，所以要在analyze_breast之后执行，否则得到的cup信息并非原始模型的
        # 因为将权重重映射到了1，所以要在set_joint_limits之前进行该操作，避免无法对小于B尺码的模型做优化
        if mode in ["DEFAULT", "CUSTOM_SIMPLIFY"]:
            remap_breast_weights(obj)

        # 设置关节限制
        cup = set_joint_limits(joint_parent, props, result=result)
        # 设置灵敏度
        set_sensitivity(rb_parent, props)

        # 导出模型
        deselect_all_objects()
        select_and_activate(root)
        if batch.suffix_type == "RGBA":
            new_filepath = os.path.join(file_dir, f"{name} {batch.suffix}.pmx")
        else:
            new_filepath = os.path.join(file_dir, f"{name} {batch.suffix} {cup}.pmx")

        if os.path.exists(new_filepath):
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

            file_name, ext = os.path.splitext(new_filepath)
            new_filepath = f"{file_name} {timestamp}{ext}"

        export_pmx(new_filepath)

        return name, "INFO", f"执行完成，模型文件地址：{new_filepath}"


def analyze_breast(root, extra_bones):
    """分析胸部尺码；无论成功还是失败，都清理分析用的临时集合"""
    data = None
    try:
        deselect_all_objects()
        select_and_activate(root)

        # 校验参数
        err_msg, data = validate_analysis_param(root, extra_bones)
        if err_msg:
            return err_msg, None

        # 预处理
        source_objs = data["source_objs"]
        preprocess(root, source_objs)
        # 原胸部顶点组名称，用于报错提示
        vg_names_src = list(dict.fromkeys(data["b_names_l"] + data["b_names_r"]))

        # 复制对象并进行切割，减少后续处理耗时
        # 重映射胸部权重，提高胸部边缘区域权重，从而提升尺码选择时的准确率
        process_source_objs(data)
        copied_obj_vgs_map = data["copied_obj_vgs_map"]

        results = batch_do_analysis(copied_obj_vgs_map, "DEFAULT", vg_names_src=vg_names_src)
        if all(r.status != AnalysisStatus.OK for r in results):
            msg = build_all_failed_msg(results)
            return msg, None

        analysis_result, _, _ = filter_results(results)
        return None, analysis_result
    finally:
        # process_source_objs 合并后会把副本从临时集合中移出，
        # 集合清理遍历不到它，这里按引用显式删除，避免残留对象及其网格/材质
        for obj in (data or {}).get("copied_source_objs", []):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
        clean_temp_data([TMP_ANALYSIS_COLLECTION_NAME])


def set_joint_limits(joint_parent, props, result=None):
    mode = props.jiggle_adjustment_mode
    cup = None
    cup_index = None

    if mode in ["DEFAULT", "CUSTOM_SIMPLIFY"]:

        breast_x = None
        breast_y = None
        breast_z = None

        if mode == "DEFAULT":
            breast_x = result.breast_x
            breast_y = result.breast_y
            breast_z = result.breast_z

            cup = result.cup
            cup_index = result.cup_index
        elif mode == "CUSTOM_SIMPLIFY":
            breast_x = props.breast_x
            breast_y = props.breast_y
            breast_z = props.breast_z

            cup, cup_index = next((c, i) for i, (limit, c) in enumerate(CUP_LIST) if breast_y * 2 * 100 < limit)

        # RGBA刚体依然保留了普通胸部刚体的结构，包括胸部骨骼、胸部刚体和胸部Joint，其余刚体与Joint仅作为辅助使用。
        # 也就是说，真正影响胸部骨骼运动的刚体是绑定到该骨骼的物理刚体，因此只需修改对应Joint的限定值即可实现抖动幅度的变化。
        # 另外，改变胸部权重会影响原本模型，导致其被修改后不适合继续作为其它流程的基模，而修改Joint限定值可以解决该问题
        # 在舞蹈动作中，胸部通常会更频繁、更剧烈地进行上下运动。因此需要对上下方向进行更严格的限制，转而通过旋转来实现自然的摇晃效果。
        # 左右和前后方向的运动通常没有那么明显，因此限制可以相对放宽一些，使胸部运动更加自然，同时避免出现过弹的情况。
        limit_lin_x_lower_mmd = -breast_x * 2 * MODEL_SCALE
        limit_lin_x_upper_mmd = breast_x * 2 * MODEL_SCALE
        limit_lin_y_lower_mmd = -breast_y * 2 * MODEL_SCALE
        limit_lin_y_upper_mmd = breast_y * 2 * MODEL_SCALE
        limit_lin_z_lower_mmd = -breast_z * 0.5 * MODEL_SCALE
        limit_lin_z_upper_mmd = breast_z * 0.5 * MODEL_SCALE
    else:
        # 自定义时，参数含义与PE一致。
        # mmd_tools 导入 PMX 时会乘 scale（=MODEL_SCALE）。
        # 导出时又会乘 1/scale 恢复回去。
        # 这里的 * MODEL_SCALE ，保证导出后与用户填写的数值一致。
        limit_lin_x_lower_mmd = props.limit_lin_x_lower * MODEL_SCALE
        limit_lin_x_upper_mmd = props.limit_lin_x_upper * MODEL_SCALE
        limit_lin_y_lower_mmd = props.limit_lin_y_lower * MODEL_SCALE
        limit_lin_y_upper_mmd = props.limit_lin_y_upper * MODEL_SCALE
        limit_lin_z_lower_mmd = props.limit_lin_z_lower * MODEL_SCALE
        limit_lin_z_upper_mmd = props.limit_lin_z_upper * MODEL_SCALE
    for joint in joint_parent.children:
        if joint.mmd_joint.name_j not in {BREAST_JP_NAME_L, BREAST_JP_NAME_R}:
            continue

        rbc = joint.rigid_body_constraint

        rbc.limit_lin_x_lower = limit_lin_x_lower_mmd
        rbc.limit_lin_x_upper = limit_lin_x_upper_mmd
        rbc.limit_lin_y_lower = limit_lin_y_lower_mmd
        rbc.limit_lin_y_upper = limit_lin_y_upper_mmd
        rbc.limit_lin_z_lower = limit_lin_z_lower_mmd
        rbc.limit_lin_z_upper = limit_lin_z_upper_mmd

        if mode == "CUSTOM":
            rbc.limit_ang_x_lower = props.limit_ang_x_lower
            rbc.limit_ang_x_upper = props.limit_ang_x_upper
            rbc.limit_ang_y_lower = props.limit_ang_y_lower
            rbc.limit_ang_y_upper = props.limit_ang_y_upper
            rbc.limit_ang_z_lower = props.limit_ang_z_lower
            rbc.limit_ang_z_upper = props.limit_ang_z_upper
        else:
            adjust_ang_limits(rbc, cup_index)

    return cup


def get_rb_bone_rel_map(rb_parent):
    rbn_bone_map = {}
    rbn_rb_map = {}
    bone_rbs_map = defaultdict(list)

    for rb in rb_parent.children:
        name = rb.name
        rbn_rb_map[name] = rb

        bone = rb.mmd_rigid.bone
        if bone:
            rbn_bone_map[name] = bone
            bone_rbs_map[bone].append(rb)

    return rbn_bone_map, rbn_rb_map, bone_rbs_map


def limb_rb_sort_key(lrb):
    """按原始手臂刚体名称排序，忽略“衝突”后缀"""
    base_name = lrb.mmd_rigid.name_j.removesuffix(COLLISION_SUFFIX)
    # 未知名称排在已知名称之后
    return LIMB_RB_NAMES.index(base_name) if base_name in LIMB_RB_NAMES else len(LIMB_RB_NAMES)


def configure_collision_mass_order(root, accessory_breast_rel_map, props):
    """
    设置胸部刚体碰撞组、修改胸饰刚体质量、对胸部刚体及Joint重排序
    创建“双臂衝突刚体”，仅对“胸部刚体”碰撞，“胸部刚体”仅对“双臂衝突刚体”碰撞
    如果其它物理刚体的碰撞组与“胸部刚体”相同，且碰巧与“双臂衝突刚体”发生碰撞，也无所谓；如果其它物理刚体的碰撞组与“双臂衝突刚体”相同，且碰巧与“胸部刚体”发生碰撞，有影响概率较低
    创建“胸部衝突刚体”，仅对物理刚体碰撞，物理刚体是否对“胸部衝突刚体”碰撞，取决于原本设置，为了尽可能减少对碰撞组的占用，“胸部衝突刚体”的碰撞组与“胸部刚体”相同
    胸部首个子骨对应的刚体如果为“物理+骨骼”类型，则改为追踪骨骼，且不与胸部碰撞，如乱破
    胸部子孙骨和胸部如果有碰撞且穿模，设置为非碰撞，如朱鸢
    """
    armature, objs, joint_parent, rb_parent = get_mmd_info(root)
    rigid_bodies = rb_parent.children
    collision = props.collision
    breast_rb_group = props.collision_group_number

    # 不论碰撞策略如何设置，第一步均先将胸部物理碰撞关闭
    for rb in rigid_bodies:
        if rb.mmd_rigid.name_j not in RGBA_RB_NAMES:
            continue
        rb.mmd_rigid.collision_group_number = breast_rb_group
        for i in range(16):
            rb.mmd_rigid.collision_group_mask[i] = True

    # 创建“双臂衝突刚体”，仅对“胸部刚体”碰撞
    limb_rb_map = {}
    if collision in ["DEFAULT"]:
        # 少前2 设置名称含“上半身”的刚体不与胸部碰撞，可能会影响其它“物理刚体”的碰撞，如头发，但几率较低
        for rb in rigid_bodies:
            if UPPER_BODY_NAME in rb.mmd_rigid.name_j:
                rb.mmd_rigid.collision_group_mask[breast_rb_group] = True

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
            crb_name = f"{name_j}{COLLISION_SUFFIX}"
            crb.mmd_rigid.name_j = crb_name
            crb.name = f"AAA_{crb_name}"
            crb.mmd_rigid.collision_group_number = LIMB_RB_GROUP
            # 仅与胸部碰撞
            for i in range(16):
                if i == breast_rb_group:
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
            if cgn == breast_rb_group:
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
            crb_name = f"{name_j}{COLLISION_SUFFIX}"
            crb.mmd_rigid.name_j = crb_name
            crb.name = f"AAA_{crb_name}"
            crb.mmd_rigid.type = "0"
            crb.mmd_rigid.collision_group_number = breast_rb_group
            # 与物理部位碰撞
            for i in cgn_set:
                crb.mmd_rigid.collision_group_mask[i] = False

        # 胸部首个子骨对应的刚体如果为“追踪骨骼”类型，则改为“物理+骨骼”
        # “追踪骨骼”类型会使胸部在MMD中乱飞，即使所有刚体全部设置为不冲突，在PE却表现正常。
        for rb in rigid_bodies:
            if rb.mmd_rigid.type != '0':  # 限定“追踪骨骼”类型
                continue
            if rb.mmd_rigid.bone not in accessory_breast_rel_map:
                continue
            rb.mmd_rigid.type = '2'
            rb.mmd_rigid.collision_group_mask[breast_rb_group] = True

        # 胸部子级和胸部如果有碰撞且穿模，设置为非碰撞，如朱鸢
        rgba_rbs = [rb for rb in rigid_bodies if rb.mmd_rigid.name_j in RGBA_RB_NAMES]
        accessory_bone_names = expand_accessory_bone_names(armature, accessory_breast_rel_map)

        # BVH 只与网格本身有关，本次循环里网格不会变，所以每个对象只建一次：
        # 胸部刚体按需建并缓存，胸饰刚体同样按需建并缓存，避免每对组合都重建
        rgba_bvh_map = {}
        accessory_bvh_map = {}

        accessory_rbs = []
        for rb in rigid_bodies:
            if rb.mmd_rigid.type not in ('1', '2'):
                continue
            if rb.mmd_rigid.name_j in RGBA_RB_NAMES:
                continue
            if rb.mmd_rigid.bone not in accessory_bone_names:
                continue
            accessory_rbs.append(rb)
            if rb.mmd_rigid.collision_group_mask[breast_rb_group] is True:
                continue
            accessory_bvh = accessory_bvh_map.get(rb.as_pointer())
            if accessory_bvh is None:
                accessory_bvh = create_bvh_tree_from_object(rb)
                accessory_bvh_map[rb.as_pointer()] = accessory_bvh
            for rgba_rb in rgba_rbs:
                rgba_bvh = rgba_bvh_map.get(rgba_rb.as_pointer())
                if rgba_bvh is None:
                    rgba_bvh = create_bvh_tree_from_object(rgba_rb)
                    rgba_bvh_map[rgba_rb.as_pointer()] = rgba_bvh
                if accessory_bvh.overlap(rgba_bvh):
                    rb.mmd_rigid.collision_group_mask[breast_rb_group] = True
                    break

        # 将胸饰刚体中的最大质量值调整为0.01，并按比例缩放其他胸饰刚体的质量
        if accessory_rbs:
            max_mass = 0.0
            for rb in accessory_rbs:
                max_mass = max(max_mass, rb.rigid_body.mass)
            if max_mass > 0:
                scale = 0.01 / max_mass
                for rb in accessory_rbs:
                    rb.rigid_body.mass *= scale

    # 刚体顺序重排序
    breast_rbs = []
    for rb in rigid_bodies:
        name_j = rb.mmd_rigid.name_j
        if name_j in RGBA_RB2_NAMES:
            breast_rbs.append(rb)
    breast_rbs.sort(key=lambda rb: RGBA_RB2_NAMES.index(rb.mmd_rigid.name_j))
    limb_rbs = sorted(limb_rb_map.values(), key=limb_rb_sort_key)
    # ZZZ (36进制) = 46655（十进制）
    for index, rb in enumerate(limb_rbs + breast_rbs):
        set_index(rb, ORDER_PREFIX_START + index)

    # Joint重排序
    breast_joints = []
    for joint in joint_parent.children:
        name_j = joint.mmd_joint.name_j
        if name_j in RGBA_JOINT_NAMES:
            breast_joints.append(joint)
    breast_joints.sort(key=lambda j: RGBA_JOINT_NAMES.index(j.mmd_joint.name_j))
    for index, rb in enumerate(breast_joints):
        set_index(rb, ORDER_PREFIX_START + index)


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
    b_rb_l = find_rigid_body(rb_parent_l, BREAST_JP_NAME_L)
    world_cos = [b_rb_l.matrix_world @ v.co for v in b_rb_l.data.vertices]
    y_min = min(co.y for co in world_cos)
    offset_y = (armature_r.matrix_world @ bone_r.tail).y - y_min

    # 移动胸部root以适配源模型
    root_l.location.y += offset_y
    root_r.location.y += offset_y

    # 应用位置
    apply_transform_to_objects([root_l, root_r], (True, False, False))


def apply_rotation_diff(root_l, armature_l, bone_l, dummy_head_lo_l, dummy_tail_lo_l,
                        root_r, armature_r, bone_r, dummy_head_lo_r, dummy_tail_lo_r):
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


def apply_scale_diff(rb_parent_l, rb_parent_r, x_r, z_r, rb_scale_factor):
    """计算RGBA胸部刚体半径与胸部区域半径的缩放差，并调整RGBA胸部刚体半径"""
    b_rb_l = find_rigid_body(rb_parent_l, BREAST_JP_NAME_L)
    b_rb_r = find_rigid_body(rb_parent_r, BREAST_JP_NAME_R)
    breast_r = (x_r + z_r) / 2
    r = b_rb_l.mmd_rigid.size[0]
    # 由于胸部并非完美球形，弥补缩放差后胸部刚体会超出实际胸部区域，所以需乘上rb_scale_factor
    # 0.6是一个较为合适的值，用于将rb_scale_factor参数归一化
    scale_factor = breast_r / r * 0.6 * rb_scale_factor

    # 缩放RGBA胸部刚体以适配源模型胸部区域
    b_rb_l.mmd_rigid.size[0] *= scale_factor
    b_rb_r.mmd_rigid.size[0] *= scale_factor


def get_dummy_breast_coords(armature, breast_bones, horizontal_bones, influenced_verts, obj):
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


def remove_breast_bones(root, armature, rb_parent, kept_joints, extra_bones):
    # 记录被删除的骨骼属于左侧还是右侧
    breast_bones_map = get_breast_bones_map(armature, extra_bones)
    breast_bones = next(iter(breast_bones_map.values()))
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
    # 为了避免误删，这里通过记录“被删除的骨骼其关联的刚体有哪些”来实现“删除骨骼时同时删除其对应刚体”的目的，而不是简单地将“未关联到骨骼的刚体”全部删除。
    rbs_to_remove = []
    # 删除冗余骨骼
    deselect_all_objects()
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature.data.edit_bones
    to_delete = [eb.name for eb in edit_bones if eb.name in breast_names]
    for name in to_delete:
        rbs_to_remove.extend(bone_rbs_map.get(name, []))

    # 删除骨骼前，先解除刚体子级约束的目标骨骼引用，避免执行过程中产生无效引用提示。
    for rb in rbs_to_remove:
        bone_name = rb.mmd_rigid.bone
        if not bone_name or bone_name not in to_delete:
            continue
        for cons in rb.constraints:
            if cons.type == "CHILD_OF":
                cons.subtarget = ""

    for name in to_delete:
        edit_bones.remove(edit_bones[name])
    bpy.ops.object.mode_set(mode='OBJECT')

    # 清理无效刚体Joint
    remove_invalid_rigidbody_joint(root, rbs_to_remove, kept_joints)
    return b_names_l, b_names_r


def join_model(armature, armature_l, armature_r, target_upper_body_name):
    deselect_all_objects()
    select_and_activate(armature_l)
    select_and_activate(armature_r)
    select_and_activate(armature)
    bpy.ops.object.mode_set(mode='POSE')
    # 取消选中源模型所有骨骼
    for pb in armature.pose.bones:
        select_pose_bone(pb, False)
    # 选中左右胸部骨骼，选中源模型“上半身2”骨骼
    for pb in armature_l.pose.bones:
        select_pose_bone(pb, True)
    for pb in armature_r.pose.bones:
        select_pose_bone(pb, True)
    for pb in armature.pose.bones:
        if pb.name == target_upper_body_name:
            select_pose_bone(pb, True)
            armature.data.bones.active = pb.bone
    # 合并模型
    bpy.ops.mmd_tools.model_join_by_bones()


def select_pose_bone(pb, status):
    """选中/取消选中姿态模式下骨骼"""
    blender_version = bpy.app.version
    # https://developer.blender.org/docs/release_notes/5.0/python_api/#animation-rigging
    # Pose bones now have a select property that stores their selection state.
    # (bpy.data.objects["Armature"].pose.bones[0].select)
    # Selection is synced with edit bones when going in and out of Edit Mode.
    if blender_version < (5, 0, 0):
        pb.bone.select = status
    else:
        pb.select = status


def bind_rb_to_body(rb_parent, target_upper_body_name):
    """
    将胸部刚体绑定到源模型的身体骨骼。
    暂对“上半身2.L”和“上半身2.R”进行冗余处理，并调整其碰撞组和尺寸。
    """
    for rb in rb_parent.children:
        name_j = rb.mmd_rigid.name_j
        if name_j in ("上半身2_L", "上半身2_R"):
            suffix = "_L" if name_j.endswith("_L") else "_R"

            rb.mmd_rigid.name_j = f"{target_upper_body_name}{suffix}"
            rb.mmd_rigid.bone = target_upper_body_name

            rb.mmd_rigid.collision_group_mask[:] = [True] * 16
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
        if bb_name is None:
            continue
        child_bone = armature.data.edit_bones.get(bbc_name)
        parent_bone = armature.data.edit_bones.get(
            BREAST_BL_NAME_L if ".L" in bb_name else BREAST_BL_NAME_R
        )
        if child_bone is None or parent_bone is None:
            continue
        child_bone.parent = parent_bone
    bpy.ops.object.mode_set(mode='OBJECT')

    # 重新连接无效关节
    b_rb_l = find_rigid_body(rb_parent, BREAST_JP_NAME_L)
    b_rb_r = find_rigid_body(rb_parent, BREAST_JP_NAME_R)

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

    # 获取胸部刚体名称列表
    breast_rb_names = [rb.name for rb in rb_parent.children if rb.mmd_rigid.bone in breast_names]

    # 与胸部刚体连接的物理刚体所对应的骨骼，也可被算作胸饰（例如：胧嫣-瑞狐 珠光曳影）
    # 这类模型虽然胸饰骨骼没有与胸部相连，但由于是物理演算和关节连接的方式，所以物理依然生效，但如果是「追踪骨骼」或者「物理+骨骼位置对齐」类型的刚体，则模型本身就会出问题，这里也不会考虑
    for joint in joint_parent.children:
        c = joint.rigid_body_constraint
        if not c:
            continue

        for obj in (c.object1, c.object2):
            other = c.object2 if obj == c.object1 else c.object1
            if (obj and obj.name in breast_rb_names
                    and other and other.name not in breast_rb_names
                    and other.mmd_rigid.type == '1' and other.mmd_rigid.bone):
                bone_name = other.mmd_rigid.bone
                pb = armature.pose.bones.get(bone_name)
                if pb:
                    accessory_breast_rel_map[bone_name] = pb.parent.name if pb.parent else None

    # 获取胸饰品刚体名称列表
    accessory_bone_names = expand_accessory_bone_names(armature, accessory_breast_rel_map)
    accessory_rb_names = [rb.name for rb in rb_parent.children if rb.mmd_rigid.bone in accessory_bone_names]

    # 记录链接胸和胸饰品的Joint，避免后续被删除，供后续修复Joint连接用
    kept_joints = {}
    for joint in joint_parent.children:
        rbc = joint.rigid_body_constraint
        if not rbc:
            continue
        object1 = rbc.object1
        object2 = rbc.object2
        if not object1 or not object2:
            continue

        if object1.name in accessory_rb_names and object2.name in breast_rb_names:
            breast_rb = object2
        elif object1.name in breast_rb_names and object2.name in accessory_rb_names:
            breast_rb = object1
        else:
            breast_rb = None

        if breast_rb:
            kept_joints[joint.name] = "L" if breast_rb.location.x > 0 else "R"
    return accessory_breast_rel_map, kept_joints


def apply_transform_to_objects(objects, trans):
    """对一组对象应用变换（仅缩放）"""
    for obj in objects:
        show_object(obj)
        deselect_all_objects()
        select_and_activate(obj)
        bpy.ops.object.transform_apply(location=trans[0], rotation=trans[1], scale=trans[2])


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


def remove_invalid_rigidbody_joint(root, rbs_to_remove, kept_joints):
    """清理无效刚体Joint"""
    active_object = bpy.context.active_object
    original_mode = active_object.mode if active_object is not None else None
    armature, objs, joint_parent, rb_parent = get_mmd_info(root)

    # 需要删除的附加刚体名单：衝突刚体 + RGBA 原始躯干刚体名 + 本次绑定的目标骨骼名
    # 目标骨骼名不一定是“上半身2”（如仅有“上半身”），动态生成才能保证重跑时删掉上一次的产物
    addon_rb = {f"{n}{COLLISION_SUFFIX}" for n in LIMB_RB_NAMES + [BREAST_JP_NAME_L, BREAST_JP_NAME_R]}
    addon_rb.update(n for n in RGBA_RB2_NAMES if n.startswith(UPPER_BODY_NAME))
    neck_bone = armature.data.bones.get(NECK_NAME)
    if neck_bone is not None and neck_bone.parent is not None:
        target_upper_body_name = neck_bone.parent.name
        addon_rb.update({f"{target_upper_body_name}_L", f"{target_upper_body_name}_R"})

    # （预先）删除无效关节
    for joint in reversed(joint_parent.children):
        if joint.name in kept_joints.keys():
            continue
        rigidbody1 = joint.rigid_body_constraint.object1
        rigidbody2 = joint.rigid_body_constraint.object2
        if any(r not in rb_parent.children for r in [rigidbody1, rigidbody2]):
            bpy.data.objects.remove(joint, do_unlink=True)

    # 处理刚体
    rbs_to_delete = set()
    for rigidbody in rb_parent.children:
        # 虽然这些刚体在删除对应骨骼时会一并被删除，但它们有可能被错误地绑定到非胸部骨骼上，所以在此强制删除
        name_j = rigidbody.mmd_rigid.name_j
        if name_j in RGBA_RB_NAMES:
            rbs_to_delete.add(rigidbody)
            continue
        match = BREAST_RB_PATTERN.match(name_j)
        if match and rigidbody.mmd_rigid.type in ('1', '2'):
            rbs_to_delete.add(rigidbody)
            continue
        # 删除衝突刚体，防止重复创建
        if name_j in addon_rb:
            rbs_to_delete.add(rigidbody)
            continue
        match = check_girlsfrontline_breast_bones_and_rbs(name_j)
        if match and rigidbody.mmd_rigid.type in ('1', '2'):
            rbs_to_delete.add(rigidbody)
            continue
        # 关联骨骼不存在则删除这个刚体
        if rigidbody.name in rbs_to_remove:
            rbs_to_delete.add(rigidbody)
            continue
        # 当刚体没有关联骨骼时，刚体可能会被Joint关联，所以不会导致问题，因此这种情况不处理，取决于模型本身
    # 统一删除刚体
    for rb_to_delete in rbs_to_delete:
        bpy.data.objects.remove(rb_to_delete, do_unlink=True)

    # 删除无效关节
    for joint in reversed(joint_parent.children):
        if joint.name in kept_joints.keys():
            continue
        rigidbody1 = joint.rigid_body_constraint.object1
        rigidbody2 = joint.rigid_body_constraint.object2
        if not rigidbody1 or not rigidbody2:
            bpy.data.objects.remove(joint, do_unlink=True)

    # 仅在确实改变了模式时才切回去
    if original_mode is not None and active_object.mode != original_mode:
        bpy.ops.object.mode_set(mode=original_mode)


def set_index(obj, index):
    m = RB_JOINT_PREFIX_REGEXP.match(obj.name)
    name = m.group('name') if m else obj.name
    obj.name = '%s_%s' % (int2base(index, ORDER_PREFIX_BASE, ORDER_PREFIX_WIDTH), name)


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

    # 获取目录，并转换与规范化，确保 //..\ 等路径格式可以正常解析
    directory = bpy.path.abspath(batch.directory)
    directory = os.path.normpath(directory)

    search_strategy = batch.search_strategy
    threshold = batch.threshold
    suffix = batch.suffix
    conflict_strategy = batch.conflict_strategy

    results = []
    total_pmx_count = 0
    for root, dirs, files in os.walk(directory):
        pmx_files = [f for f in files if f.lower().endswith(('.pmx', '.pmd'))]
        if not pmx_files:
            continue
        total_pmx_count += len(pmx_files)

        print(f"当前模型目录：{root}")
        # 原模型文件
        original_files = []
        # 原模型文件（已处理）
        processed_files = set()
        # 经检索模式和冲突时筛选后的模型文件
        selected = []
        processed_pattern = re.compile(r'^(.+)' + suffix + r'.*')
        for f in pmx_files:
            # 阈值单位为 KB，getsize 返回字节
            if os.path.getsize(os.path.join(root, f)) <= threshold * 1024:
                continue
            name_no_ext, ext = os.path.splitext(f)
            m = processed_pattern.match(name_no_ext)
            if m:
                original_name = m.groups()[0]
                original_name = original_name[:-1] if original_name.endswith(" ") else original_name
                processed_files.add(f"{original_name}{ext}")
            else:
                original_files.append(f)

        print(f"原模型文件:{original_files}")
        print(f"原模型文件（已优化过）:{processed_files}")
        if search_strategy == 'LATEST':
            if original_files:
                selected = [max(original_files, key=lambda x: os.path.getmtime(os.path.join(root, x)))]
            else:
                selected = []
        elif search_strategy == 'ALL':
            selected = original_files
        print(f"原模型文件（检索模式-{search_strategy}）：{selected}")
        if conflict_strategy == 'SKIP':
            selected = [f for f in selected if f not in processed_files]
        else:
            pass
        print(f"原模型文件（检索模式-{search_strategy} 冲突时-{conflict_strategy}）：{selected}")

        results.extend(os.path.join(root, f) for f in selected)

    msg = (f"实际待处理数量：{len(results)}。"
           f"文件总数：{total_pmx_count}，跳过数量：{total_pmx_count - len(results)}")
    print(msg)
    return results


def check_batch_props(operator, batch):
    suffix = batch.suffix
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
    # 仅简单校验下后缀是否合法
    if any(char in suffix for char in INVALID_CHARS):
        operator.report(type={'ERROR'}, message=f'Invalid name suffix!')
        return False

    return True


def round_to_two_decimals(value):
    """将数值四舍五入到小数点后两位"""
    return round(value, 2)


def create_bvh_tree_from_object(obj):
    with bmesh_from_mesh(obj.data) as bm:
        bm.transform(obj.matrix_world)
        return BVHTree.FromBMesh(bm)
