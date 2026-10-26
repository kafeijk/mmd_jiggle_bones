import math
import re

# 插件 ID 前缀，Operator 的 bl_idname 统一使用该前缀
ADDON_ID_PREFIX = "mmd_jiggle_bones"
# Panel 的 bl_idname 前缀，Blender 要求 Panel 的 id 前缀全大写
PANEL_ID_PREFIX = ADDON_ID_PREFIX.upper() + "_PT"

# 最大重试次数
MAX_RETRIES = 5
# 导入/导出失败后的重试间隔（秒）
RETRY_DELAY = 1
# 临时集合名称
TMP_COLLECTION_NAME = "胸部物理移植临时集合"
# 分析临时集合名称
TMP_ANALYSIS_COLLECTION_NAME = "尺码分析临时集合"
# 临时对象标记属性（仅尺码分析临时集合中保留的临时对象会带此标记）
TMP_OBJ_PROP = "mmd_jiggle_tmp_obj"
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
# 衝突刚体名称后缀
COLLISION_SUFFIX = "衝突"

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
    r'^([_左右胸]*)'  # 开头可为下划线、左、右、胸
    r'([_上下前後先間親亲変形回転支え基W筋]*)'
    r'([0-9０-９])?'  # 可选的单独数字（半角或全角）
    r'([錘先D])?'
    r'(\.\d{3})?'  # 可选的前置序号
    r'(\.[LR])?'  # 可选的左右标识
    r'(\.\d{3})?'  # 可选的后置序号
    r'$'
)
PHYSICAL_FRAME_NAME = "物理"

# 胸部权重阈值
WEIGHT_THRESHOLD = 0.25
# 文件名非法字符
INVALID_CHARS = '<>:"/\\|?*'
# 胸部对象临时名称
DEFAULT_TMP_OBJ_NAME = "DEFAULT_TMP"
BASEBODY_TMP_OBJ_NAME = "BASEBODY_TMP"

# 胸部尺码判定列表
# 从 B 起每次递增2，AA、A 为特殊的起始档。
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

# 胸部分析阈值序列
ANALYSIS_THRESHOLDS = (0.25, 0.2, 0.15, 0.1, 0.05, 0)

# 事件定时器间隔（秒）
TIMER_INTERVAL = 0.01

# 批处理进度标识：进度状态存放在模块级变量中，不写入 .blend
TASK_SET_RGBA = "set_rgba"
TASK_ANALYSIS = "analysis"

# 分析进度：预处理阶段还没有百分比，先显示该文字
ANALYSIS_PREPROCESS_TEXT = "预处理中..."

# 顶点坐标比较时的舍入位数
COORD_ROUND_DIGITS = 6

# 刚体/Joint 排序前缀：36 进制 3 位（ZZZ = 46655），起始值 10000
ORDER_PREFIX_BASE = 36
ORDER_PREFIX_WIDTH = 3
ORDER_PREFIX_START = 10000

# 消除环自交/凹陷的最大迭代次数
BUST_LINE_MAX_ITER = 1000

# 修正乳首时，顶点关联面的角度超出何种范围时判定为有问题
LINKED_FACES_THRESHOLD = math.radians(130)  # 100度仍不保守，调整至120度，如雷电将军 调整至130度，如苔丝_绮幻

# 模型在MMD与Blender之间的默认缩放值
MODEL_SCALE = 0.08

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
