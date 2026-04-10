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
    优先向上查找包含 'vms' 子目录的父目录（MuMu 12 核心特征）；
    若无则回退到包含主程序文件的目录。
    """
    try:
        adb_path = Path(adb_path).resolve()
    except Exception:
        return None

    current = adb_path.parent
    fallback = None

    for _ in range(6):
        if (current / "vms").is_dir():
            return current
        if fallback is None:
            if (current / "MuMuPlayer.exe").is_file() or (current / "MuMuManager.exe").is_file():
                fallback = current
        if (current / "emulator").is_dir():
            return current
        current = current.parent

    return fallback


def extract_port_from_address(address: str) -> int | None:
    """
    从 ADB 地址中提取端口号，例如 '127.0.0.1:16416' -> 16416。
    """
    if ":" in address:
        try:
            return int(address.split(":")[-1])
        except ValueError:
            pass
    return None


def find_config_file(install_path: Path, address: str | None = None) -> Path | None:
    """
    在 MuMu 安装目录中查找 customer_config.json 文件。
    如果提供了 address，则尝试根据端口精确定位到对应实例的配置文件。
    """
    vms_dir = install_path / "vms"
    if not vms_dir.exists():
        logger.debug(f"vms 目录不存在: {vms_dir}")
        return None

    port = extract_port_from_address(address) if address else None
    target_index = None
    if port is not None:
        # MuMu 12 多开端口计算：16384 + index * 32
        target_index = (port - 16384) // 32
        logger.debug(f"根据端口 {port} 计算得到目标实例索引: {target_index}")

    exact_match = None
    first_valid = None

    for instance_dir in vms_dir.iterdir():
        if not instance_dir.is_dir():
            continue

        if target_index is not None:
            if instance_dir.name.endswith(f"-{target_index}"):
                config_file = instance_dir / "configs" / "customer_config.json"
                if config_file.exists():
                    exact_match = config_file
                    break

        if first_valid is None:
            config_file = instance_dir / "configs" / "customer_config.json"
            if config_file.exists():
                first_valid = config_file

    if exact_match:
        logger.debug(f"找到精确匹配的配置文件: {exact_match}")
        return exact_match
    elif first_valid:
        if target_index is not None:
            logger.warning(
                f"未找到索引为 {target_index} 的实例配置文件，将使用首个有效配置文件: {first_valid}"
            )
        else:
            logger.debug(f"找到配置文件: {first_valid}")
        return first_valid

    return None


def get_render_mode(config_path: Path) -> str | None:
    """
    从 MuMu 配置文件中读取当前渲染模式。
    返回字符串如 "DirectX"、"Vulkan" 或 None（读取失败）。
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
    if not mode_choose:
        logger.debug("未找到渲染模式选择字段")
        return None

    # 根据 choose 字段提取对应的后端值
    # 例如 "render.mode.stable" -> "stable"
    backend_key = mode_choose.split(".")[-1]
    backend_value = render.get("mode", {}).get(backend_key)
    if backend_value:
        logger.debug(f"检测到渲染模式: {backend_value} (choose={mode_choose})")
        return backend_value

    logger.debug(f"无法从配置中提取渲染模式后端，choose={mode_choose}")
    return None


@AgentServer.tasker_sink()
class MuMuRenderChecker(TaskerEventSink):
    """
    MuMu 模拟器渲染模式检查器
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
        if noti_type != NotificationType.Starting:
            return

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

        adb_path, address = get_adb_info_from_controller(controller)
        install_path = None

        if adb_path:
            logger.debug(f"获取到 ADB 路径: {adb_path}")
            install_path = find_mumu_install_path(adb_path)

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

        config_path = find_config_file(install_path, address)
        if config_path is None:
            logger.error(
                f"🚨 在安装目录中未找到 MuMu 配置文件。"
                f"安装路径: {install_path}。"
                f"请确保 MuMu 模拟器已正确安装并至少运行过一次。"
            )
            tasker.post_stop()
            return

        render_mode = get_render_mode(config_path)
        if render_mode is None:
            logger.error(
                "🚨 无法获取 MuMu 模拟器渲染模式，请检查配置文件是否完整。"
            )
            tasker.post_stop()
            return

        if render_mode != "DirectX":
            logger.error(
                f"🚨 MuMu 模拟器渲染模式不是 DirectX！任务已停止。"
                f"当前ADB 地址: {address}的渲染模式: {render_mode}。"
                # f"配置文件: {config_path}。"
                f"请打开 MuMu 设置中心 -> 显示 -> 渲染模式，选择“DirectX”并重启模拟器。"
            )
            tasker.post_stop()
        else:
            logger.info(f"渲染模式检查通过: DirectX (ADB 地址: {address})")