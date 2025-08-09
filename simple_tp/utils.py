from typing import NamedTuple, Optional, List, Union, Iterable, Literal, Callable
import threading
from enum import Flag, auto

import mcdreforged.api.all as mcdr

import minecraft_data_api as api

import simple_tp.constants as constants

import simple_tp


class CoordWithDimension(NamedTuple):
    x: float
    y: float
    z: float
    dimension: int


class LoopManager:
    def __init__(self, run_function: Callable, interval: int):
        self.run_function = run_function
        self.interval = interval
        self._stop_event = threading.Event()
        self.thread = None

    def start(self):
        def loop():
            while not self._stop_event.wait(self.interval):
                self.run_function()

        # If a thread is already running, stop it before starting a new one
        if self.thread is not None and self.thread.is_alive():
            self.stop()
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread is not None:
            self._stop_event.set()
            self.thread.join()
            self.thread = None
            self._stop_event.clear()

def get_player_list() -> Optional[List[str]]:
    try:
        return api.get_server_player_list().players
    except Exception as e:
        simple_tp.plugin_server.logger.error(f"Error getting player list: {e}")
        return None


def get_player_dimension(
    player: str,
) -> Optional[str]:
    try:
        dimension = api.get_player_info(player, "Dimension")
    except Exception as e:
        simple_tp.plugin_server.logger.error(
            f"Error getting dimension for player {player}: {e}"
        )
        return None
    if type(dimension) is int:
        dimension = constants.DIM_ID2STR.get(
            dimension, simple_tp.plugin_config.extra_dimensions.get(dimension)
        )
        if dimension is None:
            simple_tp.plugin_server.logger.warning(
                f"Player {player} is in an unknown dimension with ID {dimension}"
            )
            return None

    return dimension


def get_player_position(
    player: str,
) -> Optional[CoordWithDimension]:
    try:
        coord = api.get_player_coordinate(player)
    except Exception as e:
        simple_tp.plugin_server.logger.error(
            f"Error getting position for player {player}: {e}"
        )
        return None

    dimension = get_player_dimension(player)

    if dimension not in simple_tp.data_manager.dimension_str2sid:
        simple_tp.plugin_server.logger.warning(
            f"Player {player} is in a dimension not enabled in config: {dimension}"
        )
        return None
    dim_sid = simple_tp.data_manager.dimension_str2sid[dimension]
    return CoordWithDimension(coord.x, coord.y, coord.z, dim_sid)


def check_permission(player: str, permission: int) -> bool:
    return simple_tp.plugin_server.get_permission_level(player) >= permission


def get_command_button(
    text: str,
    command: str,
    hover_text: Optional[str] = None,
    color: mcdr.RColor = mcdr.RColor.aqua,
    styles: Union[mcdr.RStyle, Iterable[mcdr.RStyle]] = (mcdr.RStyle.underlined,),
    type: Literal["suggest", "run"] = "run",
) -> mcdr.RText:
    if hover_text is None:
        hover_text = command
    return (
        mcdr.RText(text, color=color, styles=styles)
        .h(hover_text)
        .c(
            mcdr.RAction.suggest_command
            if type == "suggest"
            else mcdr.RAction.run_command,
            command,
        )
    )

class TpCheckFlags(Flag):
    ONLINE = auto()
    WORLD = auto()
    PERMISSION = auto()


def teleport_check(
    main_body: str,
    check_flags: TpCheckFlags,
    player: Optional[str] = None,
    player_coord: Optional[CoordWithDimension] = None,
    player_dim: Optional[str] = None,
    target_coord: Optional[CoordWithDimension] = None,
    target_dim: Optional[str] = None,
    target_player: Optional[str] = None,
):
    # player_coord 和 player_dim 只能有一个不为 None
    assert (
        player_coord is None or player_dim is None
    ), "Cannot provide player_coord and player_dim at the same time."
    # target_coord 和 target_dim 只能有一个不为 None
    assert (
        target_coord is None or target_dim is None
    ), "Cannot provide target_coord and target_dim at the same time."

    if player is None:
        player = main_body

    cache_data = {}
    if player_coord is not None:
        cache_data[player] = {
            "dim": simple_tp.data_manager.dimension_sid2str[player_coord.dimension],
        }
    if player_dim is not None:
        cache_data[player] = {
            "dim": player_dim,
        }
    if target_coord is not None and target_player is not None:
        cache_data[target_player] = {
            "dim": simple_tp.data_manager.dimension_sid2str[target_coord.dimension],
        }
    if target_dim is not None and target_player is not None:
        cache_data[target_player] = {
            "dim": target_dim,
        }

    def dim_getter(player: str) -> Optional[str]:
        if cache_data.setdefault(player, {}).get("dim") is None:
            cache_data[player]["dim"] = get_player_dimension(player)
        return cache_data[player]["dim"]

    def reply_error(msg):
        return simple_tp.plugin_server.tell(
            main_body, mcdr.RText(msg, color=constants.ERROR_COLOR)
        )

    if TpCheckFlags.ONLINE in check_flags:
        player_list = get_player_list()
        if player_list is None:
            reply_error(
                "Failed to retrieve the player list. Please ask an admin to check the server logs."
            )
            return False
        if player not in player_list:
            reply_error(f"Player {player} is not online.")
            return False
        if target_player and target_player not in player_list:
            reply_error(f"Player {target_player} is not online.")
            return False

    if TpCheckFlags.WORLD in check_flags:
        player_dim = dim_getter(player)
        if player_dim is None:
            reply_error(
                f"Failed to retrieve player {player}'s dimension. Please ask an admin to check the server logs."
            )
            return False
        if player_dim not in simple_tp.plugin_config.worlds:
            reply_error(
                f"Player {player} is in a dimension not enabled in the config: {player_dim}"
            )
            return False
        if target_player:
            target_dim = dim_getter(target_player)
            if target_dim is None:
                reply_error(
                    f"Failed to retrieve player {target_player}'s dimension. Please ask an admin to check the server logs."
                )
                return False
            if target_dim not in simple_tp.plugin_config.worlds:
                reply_error(
                    f"Player {target_player} is in a dimension not enabled in the config: {target_dim}"
                )
                return False
        if target_coord:
            target_dim = simple_tp.data_manager.dimension_sid2str[
                target_coord.dimension
            ]
            if target_dim not in simple_tp.plugin_config.worlds:
                reply_error(
                    f"Target Position is in a dimension not enabled in the config: {target_dim}"
                )
                return False
        if target_dim and target_dim not in simple_tp.plugin_config.worlds:
            reply_error(f"Target dimension is not enabled in the config: {target_dim}")
            return False

    if TpCheckFlags.PERMISSION in check_flags:
        player_dim = dim_getter(player)
        if target_player:
            target_dim = dim_getter(target_player)
        if target_coord:
            target_dim = simple_tp.data_manager.dimension_sid2str[
                target_coord.dimension
            ]
        if player_dim != target_dim and not check_permission(
            main_body, simple_tp.plugin_config.permissions.cross_world_tp
        ):
            reply_error(
                f"Player {main_body} have no permission to teleport across dimensions. ({player_dim} -> {target_dim})"
            )
            return False

    return True
