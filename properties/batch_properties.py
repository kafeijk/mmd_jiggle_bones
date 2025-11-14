import bpy


class BatchProperty(bpy.types.PropertyGroup):
    directory: bpy.props.StringProperty(
        name="模型目录",
        description="模型文件所在目录（可跨越层级）",
        subtype='DIR_PATH',
        default=''
    )
    threshold: bpy.props.IntProperty(
        name="阈值",
        description="排除体积较小的文件（单位：KB），会忽略文件体积小于该数值的文件",
        default=1024,
        min=0,
        max=1024 * 1024,
    )
    conflict_strategy: bpy.props.EnumProperty(
        name="冲突时",
        description="当模型目录中已存在由插件生成的模型文件时，如何进行后续操作",
        items=[
            ("SKIP", "跳过", "忽略对应的源模型文件，不再执行后续操作"),
            ("RE_GENERATE", "重新生成", "生成一个新的模型文件，并保留原有文件")
        ],
        default="RE_GENERATE"
    )
    suffix: bpy.props.StringProperty(
        name="名称后缀",
        description="在源文件名的基础上，为输出文件添加的名称后缀",
        default='RGBA',
        maxlen=50,  # 防止用户随意输入
    )
    suffix_dummy: bpy.props.StringProperty(
        name="名称后缀（时间戳）",
        description="在源文件名的基础上，为输出文件添加的名称后缀（时间戳）",
        default='时间戳',
        maxlen=50,  # 防止用户随意输入
    )
    search_strategy: bpy.props.EnumProperty(
        name="检索模式",
        description="如果检索到多个符合条件的文件，应该如何处理",
        items=[
            ("LATEST", "最新", "获取修改日期最新的文件"),
            ("ALL", "全部", "获取所有文件")],
        default="LATEST"
    )