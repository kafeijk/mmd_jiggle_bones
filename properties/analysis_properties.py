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

    breast_name_str: bpy.props.StringProperty(
        name="骨骼补充",
        description="通过额外的胸部骨骼名称，扩展默认胸部匹配范围\n在此处输入胸部骨骼名称，并以中文逗号或英文逗号作为分隔",
        default='breast.L,breast.R',
    )

    remove_tmp: bpy.props.BoolProperty(
        name="删除临时对象",
        description="删除临时对象",
        default=True
    )

    @staticmethod
    def register():
        bpy.types.Scene.mmd_jiggle_tools_analysis = bpy.props.PointerProperty(type=AnalysisProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_jiggle_tools_analysis
