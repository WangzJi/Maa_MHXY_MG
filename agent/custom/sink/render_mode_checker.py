"""
MuMu 模拟器渲染模式检查器

在任务开始时自动检测 MuMu 模拟器的安装路径和配置文件，
检查显卡渲染模式是否为 DirectX，如果不是则停止任务并输出警告。
"""

import json
from pathlib import Path
import winreg

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker, TaskerEventSink
from maa.event_sink import NotificationType

from utils.logger import logger


def get_adb_info_from_controller(controller) -> tuple[str | None, str | None]:
    """
    从 MAA 控制器获取 ADB 路径和设备地址。
    """
    try:
        adb_path = getattr(controller, 'adb_path', None)
        address = getattr(controller, 'address', None)
        if adb_path and address:
            return adb_path, address
    except Exception:
        pass

    try:
        from maa.toolkit import Toolkit
        devices = Toolkit.find_adb_devices()
        if devices:
            device = devices[0]
            return device.adb_path, device.address
    except Exception:
        pass

    return None, None


def get_mumu_install_path_from_registry() -> Path | None:
    """
    从 Windows 注册表获取 MuMu 模拟器的安装路径。
    """
    registry_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Netease\MuMuPlayer"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Netease\MuMuPlayer"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Netease\MuMuPlayer"),
    ]
    for hive, subkey in registry_paths:
        try:
            key = winreg.OpenKey(hive, subkey)
            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            if install_path and Path(install_path).exists():
                return Path(install_path)
        except (FileNotFoundError, OSError):
            continue
    return None


def find_mumu_install_path(adb_path: str) -> Path | None:
    """
    根据 ADB 路径查找 MuMu 模拟器的安装根目录。
    """
    adb_path = Path(adb_path)
    
    # MuMu 12: adb.exe 在 shell 目录下
    if adb_path.parent.name.lower() == "shell":
        return adb_path.parent.parent
    
    # MuMu 5.0: 向上查找包含特定名称的父目录
    for parent in adb_path.parents:
        if parent.name.lower() in ("mumuplayer", "mumu", "nemu"):
            return parent
    
    return None


def find_config_file(install_path: Path) -> Path | None:
    """
    在 MuMu 安装目录中查找 customer_config.json 文件。
    """
    vms_dir = install_path / "vms"
    if not vms_dir.exists():
        logger.debug(f"vms 目录不存在: {vms_dir}")
        return None

    # 遍历 vms 目录下的所有实例文件夹，查找配置文件
    for instance_dir in vms_dir.iterdir():
        if not instance_dir.is_dir():
            continue
        
        config_file = instance_dir / "configs" / "customer_config.json"
        if config_file.exists():
            logger.debug(f"找到配置文件: {config_file}")
            return config_file

    return None


def get_render_mode(config_path: Path) -> str | None:
    """
    从 MuMu 配置文件中读取当前渲染模式。
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"MuMu 配置文件不存在: {config_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"MuMu 配置文件解析失败: {e}")
        return None

    render = config.get("setting", {}).get("render", {})
    mode_choose = render.get("mode", {}).get("choose")
    if mode_choose != "render.mode.stable":
        logger.debug(f"渲染模式选择不是 stable，实际选择为: {mode_choose}")
        return None

    return render.get("mode", {}).get("stable")


@AgentServer.tasker_sink()
class MuMuRenderChecker(TaskerEventSink):
    """
    MuMu 模拟器渲染模式检查器
    在任务开始时自动检测配置文件路径并检查渲染模式是否为 DirectX
    """

    def __init__(self):
        super().__init__()
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

        controller = tasker.controller
        if controller is None:
            logger.error("无法获取控制器，跳过渲染模式检查")
            return

        # 1. 尝试从控制器获取 ADB 路径并推导安装目录
        adb_path, address = get_adb_info_from_controller(controller)
        install_path = None
        
        if adb_path:
            logger.debug(f"获取到 ADB 路径: {adb_path}")
            install_path = find_mumu_install_path(adb_path)
        
        # 2. 如果通过 ADB 路径找不到，尝试注册表
        if install_path is None:
            logger.debug("通过 ADB 路径无法定位安装目录，尝试从注册表获取")
            install_path = get_mumu_install_path_from_registry()
        
        if install_path is None:
            logger.error(
                "🚨 无法定位 MuMu 模拟器安装路径，请确保 MuMu 模拟器已正确安装。"
            )
            tasker.post_stop()
            return

        logger.debug(f"MuMu 安装路径: {install_path}")

        # 3. 查找配置文件
        config_path = find_config_file(install_path)
        if config_path is None:
            logger.error(
                f"🚨 在安装目录中未找到 MuMu 配置文件。\n"
                f"安装路径: {install_path}\n"
                f"请确保 MuMu 模拟器已正确安装并至少运行过一次。"
            )
            tasker.post_stop()
            return

        # 4. 检查渲染模式
        render_mode = get_render_mode(config_path)
        if render_mode is None:
            logger.error(
                "🚨 无法获取 MuMu 模拟器渲染模式，请检查配置文件是否完整。"
            )
            tasker.post_stop()
            return

        if render_mode != "DirectX":
            logger.error(
                f"🚨 MuMu 模拟器渲染模式不是 DirectX！任务已停止。\n"
                f"当前模式: {render_mode}\n"
                f"配置文件: {config_path}\n"
                f"请打开 MuMu 设置中心 -> 显示 -> 渲染模式，选择“DirectX”并重启模拟器。"
            )
            tasker.post_stop()
        else:
            logger.info(f"渲染模式检查通过: DirectX (配置文件: {config_path})")