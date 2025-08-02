from typing import NamedTuple, Optional, List, Union, Iterable, Literal

import mcdreforged.api.all as mcdr

import minecraft_data_api as api

import simple_tp.constants as constants


class CoordWithDimension(NamedTuple):
    x: float
    y: float
    z: float
    dimension: int


def get_player_position(
    player: str, server: mcdr.PluginServerInterface, worlds: List[str]
) -> Optional[CoordWithDimension]:
    try:
        coord = api.get_player_coordinate(player)
        dimension = api.get_player_info(player, "Dimension")
    except Exception as e:
        server.logger.error(f"Error getting position for player {player}: {e}")
        return None
    if type(dimension) is int:
        dimension = constants.DIM_ID2STR.get(dimension)
    if dimension not in worlds:
        server.logger.warning(
            f"Player {player} is in a dimension not enabled in config: {dimension}"
        )
        return None
    dim_id = worlds.index(dimension)
    return CoordWithDimension(coord.x, coord.y, coord.z, dim_id)


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
