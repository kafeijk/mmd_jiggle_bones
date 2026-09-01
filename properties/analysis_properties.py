import math

import bpy

from .batch_properties import BatchProperty


class AnalysisProperty(bpy.types.PropertyGroup):
    type: bpy.props.EnumProperty(
        name="分析方式",
        description="通过何种方式分析模型胸部",
        items=[
            ("DEFAULT", "通用", "适用于全模型，准确性适中"),
            ("BASEBODY", "素体", "适用于素体，准确性较好"),
        ],
        default="DEFAULT",
    )

    extra_bones_str: bpy.props.StringProperty(
        name="骨骼扩展",
        description="通过额外的胸部骨骼名称，扩展默认胸部匹配范围\n在此处输入胸部骨骼名称，并以中文逗号或英文逗号作为分隔",
        default='breast.L,breast.R',
        update=lambda self, context: self.sync_extra(context),
    )

    remove_tmp: bpy.props.BoolProperty(
        name="删除临时对象",
        description="删除临时对象",
        default=True
    )

    progress: bpy.props.FloatProperty(
        name="进度",
        default=0.0,
        min=0.0,
        max=1.0,
    )

    progress_current: bpy.props.IntProperty(
        name="当前文件",
        default=0,
    )

    progress_total: bpy.props.IntProperty(
        name="文件总数",
        default=0,
    )

    progress_message: bpy.props.StringProperty(
        name="进度信息",
        default="",
    )

    is_processing: bpy.props.BoolProperty(
        name="处理中",
        default=False,
    )

    @staticmethod
    def register():
        bpy.types.Scene.mmd_jiggle_tools_analysis = bpy.props.PointerProperty(type=AnalysisProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_jiggle_tools_analysis

    def sync_extra(self, context):
        target = context.scene.mmd_jiggle_tools_set_rgba
        if target.extra_bones_str == self.extra_bones_str:
            return
        target.extra_bones_str = self.extra_bones_str
