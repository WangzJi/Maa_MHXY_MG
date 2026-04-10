"""
MuMu 模拟器渲染模式检查器

在任务开始时检查 MuMu 模拟器的显卡渲染模式是否为 DirectX，
如果不是则停止任务并输出警告。
"""

import configparser
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker, TaskerEventSink
from maa.event_sink import NotificationType

from utils.logger import logger

# MuMu 模拟器配置文件可能的路径模板（按优先级排序）
MUMU_CONFIG_PATHS = [
    # MuMu 12 常见路径
    Path.home() / "Documents" / "MuMu12" / "emulator_name" / "config.ini",
    Path.home() / "MuMu12" / "emulator_name" / "config.ini",
    # MuMu 6 常见路径
    Path.home() / "Documents" / "MuMu" / "emulator_name" / "config.ini",
    Path.home() / "MuMu" / "emulator_name" / "config.ini",
]

# 配置文件中表示渲染模式的键（不同版本可能不同）
RENDER_MODE_KEYS = ["render_mode", "graphics_render_mode", "graphics_mode"]
# 表示 DirectX 模式的值（不区分大小写）
DIRECTX_VALUES = ["dx", "directx", "dx11"]


def find_mumu_config(instance_name: str) -> Optional[Path]:
    """
    根据模拟器实例名称查找 MuMu 配置文件路径。
    """
    for template in MUMU_CONFIG_PATHS:
        path = Path(str(template).replace("emulator_name", instance_name))
        if path.exists():
            return path
    return None


def get_mumu_instance_name_from_adb(adb_serial: str) -> Optional[str]:
    """
    根据 ADB 设备序列号（如 127.0.0.1:7555）推断 MuMu 模拟器实例名称。
    MuMu 实例端口与实例名称的对应关系：
    - 实例0: 7555 -> "MuMu" (默认)
    - 实例1: 7557 -> "MuMu1"
    - 实例2: 7559 -> "MuMu2"
    - 依此类推
    如果端口无法识别，返回 None。
    """
    try:
        if ":" in adb_serial:
            port = int(adb_serial.split(":")[-1])
        else:
            # 可能是 emulator-5554 格式
            return None
        # MuMu 默认实例端口 7555，后续实例 +2
        if port >= 7555 and (port - 7555) % 2 == 0:
            index = (port - 7555) // 2
            if index == 0:
                return "MuMu"
            else:
                return f"MuMu{index}"
    except (ValueError, IndexError):
        pass
    return None


def check_render_mode_via_config(config_path: Path) -> Optional[bool]:
    """
    从配置文件中读取渲染模式，返回 True 表示 DirectX，False 表示其他，None 表示读取失败。
    """
    try:
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        # 遍历可能的段和键
        for section in config.sections():
            for key in RENDER_MODE_KEYS:
                if key in config[section]:
                    value = config[section][key].strip().lower()
                    logger.debug(f"找到渲染模式配置: {section}.{key} = {value}")
                    return value in DIRECTX_VALUES
    except Exception as e:
        logger.error(f"读取配置文件失败 {config_path}: {e}")
    return None


def check_render_mode_via_mumumanager(instance_name: str) -> Optional[bool]:
    """
    尝试使用 MuMuManager 命令行工具获取渲染模式。
    返回 True 表示 DirectX，False 表示其他，None 表示失败。
    MuMuManager 路径通常在 MuMu 安装目录的 shell 文件夹下。
    """
    # 常见 MuMu 安装路径
    possible_paths = [
        Path("C:/Program Files/Netease/MuMu12/shell/MuMuManager.exe"),
        Path("C:/Program Files (x86)/Netease/MuMu/shell/MuMuManager.exe"),
        Path.home() / "AppData/Local/Programs/MuMu12/shell/MuMuManager.exe",
    ]
    manager_path = None
    for p in possible_paths:
        if p.exists():
            manager_path = p
            break
    if not manager_path:
        logger.debug("未找到 MuMuManager.exe")
        return None

    try:
        # 执行命令获取实例信息
        cmd = [str(manager_path), "info", "-v", instance_name]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, encoding="utf-8"
        )
        if result.returncode != 0:
            logger.debug(f"MuMuManager 执行失败: {result.stderr}")
            return None
        output = result.stdout
        # 解析输出中的渲染模式字段（格式示例: "RenderMode: dx" 或 "graphics_render_mode: dx"）
        for line in output.splitlines():
            line_lower = line.strip().lower()
            for key in RENDER_MODE_KEYS:
                if key in line_lower and ":" in line_lower:
                    parts = line_lower.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value in DIRECTX_VALUES:
                            logger.debug(f"MuMuManager 检测到 DirectX 模式")
                            return True
                        else:
                            logger.debug(f"MuMuManager 检测到非 DirectX 模式: {value}")
                            return False
    except Exception as e:
        logger.error(f"调用 MuMuManager 异常: {e}")
    return None


def is_render_mode_directx(controller) -> Tuple[bool, str]:
    """
    检查当前 MuMu 模拟器的渲染模式是否为 DirectX。
    返回 (is_directx, message)
    """
    # 获取 ADB 序列号以推断实例名称
    try:
        adb_serial = controller.adb_serial
        if not adb_serial:
            return False, "无法获取 ADB 序列号"
    except AttributeError:
        return False, "控制器不支持 adb_serial 属性"

    instance_name = get_mumu_instance_name_from_adb(adb_serial)
    if not instance_name:
        return False, f"无法从 ADB 序列号 {adb_serial} 推断 MuMu 实例名称"

    logger.debug(f"推断的 MuMu 实例名称: {instance_name}")

    # 方法1: 通过配置文件检查
    config_path = find_mumu_config(instance_name)
    if config_path:
        logger.debug(f"找到配置文件: {config_path}")
        is_dx = check_render_mode_via_config(config_path)
        if is_dx is not None:
            if is_dx:
                return True, ""
            else:
                return False, f"配置文件显示非 DirectX 模式（实例: {instance_name}）"
    else:
        logger.debug(f"未找到实例 {instance_name} 的配置文件")

    # 方法2: 尝试使用 MuMuManager
    is_dx = check_render_mode_via_mumumanager(instance_name)
    if is_dx is not None:
        if is_dx:
            return True, ""
        else:
            return False, f"MuMuManager 报告非 DirectX 模式（实例: {instance_name}）"

    # 两种方法都失败，无法确定
    return False, f"无法确定 MuMu 模拟器 {instance_name} 的渲染模式，请手动确认是否为 DirectX"


@AgentServer.tasker_sink()
class RenderModeChecker(TaskerEventSink):
    """
    MuMu 模拟器渲染模式检查器
    在任务开始时检查是否为 DirectX 模式，否则停止任务
    """

    def __init__(self):
        self._checked = False

    def on_tasker_task(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ):
        # 只在任务开始时检查
        if noti_type != NotificationType.Starting:
            return

        # 忽略停止任务事件
        if detail.entry == "MaaTaskerPostStop":
            logger.debug("收到 PostStop 事件，跳过渲染模式检查")
            return

        logger.debug(
            f"任务开始前检查渲染模式 - task_id: {detail.task_id}, entry: {detail.entry}"
        )

        # 获取控制器
        controller = tasker.controller
        if controller is None:
            logger.error("无法获取控制器")
            return

        # 执行检查
        is_dx, msg = is_render_mode_directx(controller)
        if not is_dx:
            error_msg = (
                f"🚨 {msg}。Maa_MHXY_MG 要求 MuMu 模拟器使用 DirectX 渲染模式，"
                f"请在模拟器设置中切换为 DirectX 后重启模拟器。"
            )
            logger.error(error_msg)
            # 停止任务
            tasker.post_stop()
        else:
            logger.info("渲染模式检查通过: DirectX")