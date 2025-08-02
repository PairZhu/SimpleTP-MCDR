from typing import Dict, List, Literal, Union, Iterable, Optional, NamedTuple
from readerwriterlock.rwlock import RWLockFair

import minecraft_data_api as api
import mcdreforged.api.all as mcdr


import simple_tp.constants as constants


class Config(mcdr.Serializable):
    command_prefix: str = "!!stp"
    back_on_death: bool = True

    class __Permissions(mcdr.Serializable):
        back: int = 1
        tpa: int = 1
        tpahere: int = 1
        tp: int = 2
        tphere: int = 2
        tp_xyz: int = 2
        personal_waypoint: int = 1
        global_waypoint: int = 2
        cross_world_tp: int = 1

    permissions: __Permissions = __Permissions()

    worlds: List[str] = [
        "minecraft:overworld",
        "minecraft:the_nether",
        "minecraft:the_end",
    ]


config: Config


class SimpleTPData(mcdr.Serializable):
    personal_waypoints: Dict[str, Dict[str, List[Union[float, int]]]] = {}
    global_waypoints: Dict[str, List[Union[float, int]]] = {}


class CoordWithDimension(NamedTuple):
    x: float
    y: float
    z: float
    dimension: int


class DataManager:
    def __init__(self, data: SimpleTPData):
        self._global_waypoints: Dict[str, CoordWithDimension] = {
            name: CoordWithDimension(
                coords[0],
                coords[1],
                coords[2],
                int(coords[3]) if len(coords) > 3 else 0,
            )
            for name, coords in data.global_waypoints.items()
        }
        self._personal_waypoints: Dict[str, Dict[str, CoordWithDimension]] = {}
        for player, waypoints in data.personal_waypoints.items():
            self._personal_waypoints[player] = {
                name: CoordWithDimension(
                    coords[0],
                    coords[1],
                    coords[2],
                    int(coords[3]) if len(coords) > 3 else 0,
                )
                for name, coords in waypoints.items()
            }
        self._global_rwlock = RWLockFair()
        self._personal_rwlock: Dict[str, RWLockFair] = {}
        self._personal_locks_rwlock = RWLockFair()

    def get_personal_lock(self, player: str) -> RWLockFair:
        with self._personal_locks_rwlock.gen_rlock():
            if player in self._personal_rwlock:
                return self._personal_rwlock[player]

        lock = RWLockFair()
        with self._personal_locks_rwlock.gen_wlock():
            self._personal_rwlock[player] = lock
        return lock

    def get_global_waypoints(self) -> Dict[str, CoordWithDimension]:
        with self._global_rwlock.gen_rlock():
            return self._global_waypoints.copy()

    def get_personal_waypoints(self, player: str) -> Dict[str, CoordWithDimension]:
        lock = self.get_personal_lock(player)
        with lock.gen_rlock():
            return self._personal_waypoints.get(player, {}).copy()

    def set_global_waypoints(self, waypoints: Dict[str, CoordWithDimension]):
        with self._global_rwlock.gen_wlock():
            self._global_waypoints = waypoints

    def set_personal_waypoints(
        self, player: str, waypoints: Dict[str, CoordWithDimension]
    ):
        lock = self.get_personal_lock(player)
        with lock.gen_wlock():
            self._personal_waypoints[player] = waypoints

    def delete_global_waypoint(self, waypoint_name: str):
        with self._global_rwlock.gen_wlock():
            if waypoint_name in self._global_waypoints:
                del self._global_waypoints[waypoint_name]

    def delete_personal_waypoint(self, player: str, waypoint_name: str):
        lock = self.get_personal_lock(player)
        with lock.gen_wlock():
            if waypoint_name in self._personal_waypoints[player]:
                del self._personal_waypoints[player][waypoint_name]

    def get_simple_tp_data(self) -> SimpleTPData:
        data = SimpleTPData()
        with self._global_rwlock.gen_rlock():
            data.global_waypoints = {
                name: [coord.x, coord.y, coord.z, coord.dimension]
                for name, coord in self._global_waypoints.items()
            }
        with self._personal_locks_rwlock.gen_rlock():
            data.personal_waypoints = {
                player: {
                    name: [coord.x, coord.y, coord.z, coord.dimension]
                    for name, coord in waypoints.items()
                }
                for player, waypoints in self._personal_waypoints.items()
            }
        return data


data_manager: DataManager

plugin_server: mcdr.PluginServerInterface


def on_load(server: mcdr.PluginServerInterface, prev_module: any):
    global config, data_manager, plugin_server
    plugin_server = server
    config = plugin_server.load_config_simple("config.json", target_class=Config)
    simple_tp_data = plugin_server.load_config_simple(
        "data.json", target_class=SimpleTPData
    )
    data_manager = DataManager(simple_tp_data)
    plugin_server.logger.debug(f"SimpleTP plugin loaded with config: {config}")

    if config.back_on_death:
        plugin_server.register_event_listener("PlayerDeathEvent", on_player_death)

    plugin_server.register_command(
        mcdr.Literal(config.command_prefix)
        .then(
            mcdr.Literal("setp")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.personal_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name").runs(
                    lambda src, ctx: set_waypoint(
                        src, ctx.get("waypoint_name"), is_global=False
                    )
                )
            )
        )
        .then(
            mcdr.Literal("setg")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.global_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name").runs(
                    lambda src, ctx: set_waypoint(
                        src, ctx.get("waypoint_name"), is_global=True
                    )
                )
            )
        )
        .then(
            mcdr.Literal("tpp")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.personal_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name")
                .suggests(
                    lambda src: data_manager.get_personal_waypoints(src.player).keys()
                )
                .runs(
                    lambda src, ctx: teleport_to_waypoint(
                        src, ctx.get("waypoint_name"), is_global=False
                    )
                )
            )
        )
        .then(
            mcdr.Literal("tpg")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.global_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name")
                .suggests(lambda: data_manager.get_global_waypoints().keys())
                .runs(
                    lambda src, ctx: teleport_to_waypoint(
                        src, ctx.get("waypoint_name"), is_global=True
                    )
                )
            )
        )
        .then(
            mcdr.Literal("delp")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.personal_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name")
                .suggests(
                    lambda src: data_manager.get_personal_waypoints(src.player).keys()
                )
                .runs(
                    lambda src, ctx: delete_waypoint(
                        src, ctx.get("waypoint_name"), is_global=False
                    )
                )
            )
        )
        .then(
            mcdr.Literal("delg")
            .precondition(
                lambda src: src.has_permission(config.permissions.global_waypoint)
            )
            .then(
                mcdr.Text("waypoint_name").runs(
                    lambda src, ctx: delete_waypoint(
                        src, ctx.get("waypoint_name"), is_global=True
                    )
                )
            )
        )
        .then(
            mcdr.Literal("list").runs(
                lambda src: src.reply(get_waypoints_messages(src))
            )
        )
        .then(
            mcdr.Literal("listp")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .runs(lambda src: src.reply(get_waypoints_messages(src, scope="personal")))
        )
        .then(
            mcdr.Literal("listg")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.global_waypoint)
            )
            .runs(lambda src: src.reply(get_waypoints_messages(src, scope="global")))
        )
        .then(
            mcdr.Literal("back")
            .requires(lambda src: src.is_player, lambda: constants.NOT_PLAYER_TIP)
            .precondition(
                lambda src: src.has_permission(config.permissions.personal_waypoint)
            )
            .runs(
                lambda src: teleport_to_waypoint(
                    src, constants.BACK_WAYPOINT_ID, is_global=False, enable_back=True
                )
            )
        )
    )


def get_player_position(player: str) -> Optional[CoordWithDimension]:
    try:
        coord = api.get_player_coordinate(player)
        dimension = api.get_player_info(player, "Dimension")
    except Exception as e:
        plugin_server.logger.error(f"Error getting position for player {player}: {e}")
        return None
    if type(dimension) is int:
        dimension = constants.DIM_ID2STR.get(dimension)
    if dimension not in config.worlds:
        plugin_server.logger.warning(
            f"Player {player} is in a dimension not enabled in config: {dimension}"
        )
        return None
    dim_id = config.worlds.index(dimension)
    return CoordWithDimension(coord.x, coord.y, coord.z, dim_id)


@mcdr.new_thread("on_player_death")
def on_player_death(server: mcdr.PluginServerInterface, player: str, event: str, _):
    death_position = get_player_position(player)
    if death_position is None:
        server.tell(
            player,
            mcdr.RText(
                "Failed to retrieve your position. Please ask an admin to check the server logs.",
                constants.ERROR_COLOR,
            ),
        )
        return

    personal_waypoints = data_manager.get_personal_waypoints(player)
    personal_waypoints[constants.BACK_WAYPOINT_ID] = CoordWithDimension(
        death_position.x, death_position.y, death_position.z, death_position.dimension
    )
    data_manager.set_personal_waypoints(player, personal_waypoints)


@mcdr.new_thread("delete_waypoint")
def delete_waypoint(
    source: mcdr.CommandSource,
    waypoint_name: str,
    is_global: bool,
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                "Please provide a name for the waypoint.", color=constants.ERROR_COLOR
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID:
        source.reply(
            mcdr.RText(
                f"'{constants.BACK_WAYPOINT_ID}' is a reserved waypoint name and cannot be deleted.",
                color=constants.ERROR_COLOR,
            )
        )
        return

    if is_global:
        waypoint_dict = data_manager.get_global_waypoints()
    else:
        if not source.is_player:
            source.reply(
                mcdr.RText(
                    "This command can only be used by players.",
                    color=constants.ERROR_COLOR,
                )
            )
            return
        assert isinstance(source, mcdr.PlayerCommandSource)
        waypoint_dict = data_manager.get_personal_waypoints(source.player)

    if waypoint_name not in waypoint_dict:
        source.reply(
            mcdr.RText(
                f"Waypoint '{waypoint_name}' does not exist in {'global' if is_global else 'your personal'} waypoints.",
                color=constants.ERROR_COLOR,
            )
        )
        return

    del waypoint_dict[waypoint_name]
    if is_global:
        data_manager.set_global_waypoints(waypoint_dict)
    else:
        assert isinstance(source, mcdr.PlayerCommandSource)
        data_manager.set_personal_waypoints(source.player, waypoint_dict)
    source.reply(
        mcdr.RText(
            f"Waypoint '{waypoint_name}' has been deleted successfully.",
            color=constants.SUCCESS_COLOR,
        )
    )


@mcdr.new_thread("teleport_to_waypoint")
def teleport_to_waypoint(
    source: mcdr.PlayerCommandSource,
    waypoint_name: str,
    is_global: bool,
    enable_back: bool = False,
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                "Please provide a name for the waypoint.", color=constants.ERROR_COLOR
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID and not enable_back:
        source.reply(
            mcdr.RText(
                f"'{constants.BACK_WAYPOINT_ID}' is a reserved waypoint name and cannot be used.",
                color=constants.ERROR_COLOR,
            )
        )
        return

    player = source.player

    if is_global:
        waypoint_dict = data_manager.get_global_waypoints()
    else:
        waypoint_dict = data_manager.get_personal_waypoints(player)

    if waypoint_name not in waypoint_dict:
        if waypoint_name != constants.BACK_WAYPOINT_ID:
            source.reply(
                mcdr.RText(
                    f"Waypoint '{waypoint_name}' does not exist in {'global' if is_global else 'your personal'} waypoints.",
                    color=constants.ERROR_COLOR,
                )
            )
        else:
            source.reply(
                mcdr.RText(
                    "No record of previous position found. Use it after teleporting"
                    + " or dying."
                    if config.back_on_death
                    else ".",
                    color=constants.ERROR_COLOR,
                )
            )
        return

    cur_position = get_player_position(player)
    if cur_position is None:
        source.reply(
            mcdr.RText(
                "Failed to retrieve your position. Please ask an admin to check the server logs.",
                constants.ERROR_COLOR,
            )
        )
        return

    if cur_position.dimension != waypoint_dict[
        waypoint_name
    ].dimension and not source.has_permission(config.permissions.cross_world_tp):
        source.reply(
            mcdr.RText(
                "You have no permission to teleport across dimensions.",
                color=constants.ERROR_COLOR,
            )
        )
        return

    position = waypoint_dict[waypoint_name]
    plugin_server.execute(
        f"execute in {config.worlds[position.dimension]} run tp {player} {position.x} {position.y} {position.z}"
    )
    source.reply(
        mcdr.RText(
            f"Teleporting to waypoint '{waypoint_name}': {config.worlds[position.dimension]}({position.x:.2f}, {position.y:.2f}, {position.z:.2f})",
            color=constants.SUCCESS_COLOR,
        )
    )

    if is_global:
        personal_waypoints = data_manager.get_personal_waypoints(player)
    else:
        personal_waypoints = waypoint_dict
    personal_waypoints[constants.BACK_WAYPOINT_ID] = cur_position
    data_manager.set_personal_waypoints(player, personal_waypoints)


@mcdr.new_thread("create_waypoint")
def set_waypoint(
    source: mcdr.PlayerCommandSource,
    waypoint_name: str,
    is_global: bool,
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                "Please provide a name for the waypoint.", color=constants.ERROR_COLOR
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID:
        source.reply(
            mcdr.RText(
                f"'{constants.BACK_WAYPOINT_ID}' is a reserved waypoint name and cannot be used.",
                color=constants.ERROR_COLOR,
            )
        )
        return

    player = source.player
    position = get_player_position(player)
    if position is None:
        source.reply(
            mcdr.RText(
                "Failed to retrieve your position. Please ask an admin to check the server logs.",
                constants.ERROR_COLOR,
            )
        )
        return

    if is_global:
        waypoint_dict = data_manager.get_global_waypoints()
    else:
        waypoint_dict = data_manager.get_personal_waypoints(player)
    if waypoint_name in waypoint_dict:
        old_position = waypoint_dict[waypoint_name]
        source.reply(
            mcdr.RText(
                f"Waypoint '{waypoint_name}': {config.worlds[old_position.dimension]}({old_position.x:.2f}, {old_position.y:.2f}, {old_position.z:.2f}) already exists, and will be overwritten.",
                color=constants.WARNING_COLOR,
            )
        )
    waypoint_dict[waypoint_name] = position
    if is_global:
        data_manager.set_global_waypoints(waypoint_dict)
    else:
        data_manager.set_personal_waypoints(player, waypoint_dict)
    source.reply(
        mcdr.RText(
            f"Waypoint '{waypoint_name}' successfully set to your current position: {config.worlds[position.dimension]}({position.x:.2f}, {position.y:.2f}, {position.z:.2f})",
            color=constants.SUCCESS_COLOR,
        )
    )


def get_waypoints_messages(
    source: mcdr.CommandSource, scope: Literal["personal", "global", "all"] = "all"
) -> mcdr.RText:
    def get_dim_color(dim_id: int) -> mcdr.RColor:
        if dim_id >= len(constants.DIM_COLORS):
            return constants.DIM_COLORS[
                -1
            ]  # Fallback to last color if dim_id is out of range
        return constants.DIM_COLORS[dim_id]

    replyTextLines: List[mcdr.RText] = []
    if source.is_player and scope != "global":
        assert isinstance(source, mcdr.PlayerCommandSource)
        replyTextLines.append(
            mcdr.RText("[Personal Waypoints]", color=mcdr.RColor.light_purple)
        )
        waypoints = data_manager.get_personal_waypoints(source.player)
        if not waypoints:
            replyTextLines.append(
                mcdr.RText("No personal waypoints found.", color=mcdr.RColor.gray)
            )
        for name, pos in waypoints.items():
            if name == constants.BACK_WAYPOINT_ID:
                continue

            rtext = mcdr.RText(name, color=get_dim_color(pos.dimension)) + mcdr.RText(
                f": {config.worlds[pos.dimension]}({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})",
                color=mcdr.RColor.gray,
            )

            if source.is_player:
                rtext += (
                    "  "
                    + get_command_button(
                        "[TP]",
                        f"{config.command_prefix} tpp {name}",
                        hover_text="Click to teleport to this waypoint",
                    )
                    + " "
                    + get_command_button(
                        "[DEL]",
                        f"{config.command_prefix} delp {name}",
                        hover_text="Click to input the delete command",
                        type="suggest",
                        color=mcdr.RColor.red,
                    )
                )

            replyTextLines.append(rtext)

    if scope != "personal":
        replyTextLines.append(
            mcdr.RText("[Global Waypoints]", color=mcdr.RColor.light_purple)
        )
        waypoints = data_manager.get_global_waypoints()
        if not waypoints:
            replyTextLines.append(
                mcdr.RText("No global waypoints found.", color=mcdr.RColor.gray)
            )
        for name, pos in waypoints.items():
            rtext = mcdr.RText(name, color=get_dim_color(pos.dimension)) + mcdr.RText(
                f": {config.worlds[pos.dimension]}({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})",
                color=mcdr.RColor.gray,
            )
            if source.is_player:
                rtext += "  " + get_command_button(
                    "[TP]",
                    f"{config.command_prefix} tpg {name}",
                    hover_text="Click to teleport to this waypoint",
                )
                if source.has_permission(config.permissions.global_waypoint):
                    rtext += " " + get_command_button(
                        "[DEL]",
                        f"{config.command_prefix} delg {name}",
                        hover_text="Click to input the delete command",
                        type="suggest",
                        color=mcdr.RColor.red,
                    )
            replyTextLines.append(rtext)

    return mcdr.RTextBase.join("\n", replyTextLines)


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


def on_unload(server: mcdr.PluginServerInterface):
    plugin_server.save_config_simple(data_manager.get_simple_tp_data(), "data.json")
