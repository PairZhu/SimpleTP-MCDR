import json
from typing import List, Literal, Optional, Dict
from dataclasses import dataclass
import threading
import time

import mcdreforged.api.all as mcdr

import simple_tp.constants as constants
import simple_tp.utils as utils

from simple_tp.data import SimpleTPData, DataManager
from simple_tp.config import Config
from simple_tp.online_player import OnlinePlayerCounter


@dataclass(frozen=True)
class TeleportRequest:
    player: str
    target_player: str
    timestamp: float
    is_reversed: bool  # 是否是反向传送请求


class TeleportRequestManager:
    def __init__(self):
        self._request_sender_dict: Dict[str, TeleportRequest] = {}
        self._request_receiver_dict: Dict[str, Dict[str, TeleportRequest]] = {}
        self._lock = threading.RLock()

    def set_request(
        self,
        tp_request: TeleportRequest,
        fail_if_exists: bool = True,
    ) -> Optional[TeleportRequest]:
        player = tp_request.player
        target_player = tp_request.target_player
        with self._lock:
            previous_request = self._request_sender_dict.get(player)
            if previous_request:
                if fail_if_exists:
                    return previous_request
                else:
                    self.remove_request(previous_request)
            self._request_receiver_dict.setdefault(target_player, {})[player] = (
                tp_request
            )
            self._request_sender_dict[player] = tp_request
            return previous_request

    def remove_request(self, tp_request: TeleportRequest):
        with self._lock:
            if tp_request.player in self._request_sender_dict:
                del self._request_sender_dict[tp_request.player]
            if tp_request.target_player not in self._request_receiver_dict:
                return
            receiver_requests = self._request_receiver_dict[tp_request.target_player]
            if tp_request.player in receiver_requests:
                del receiver_requests[tp_request.player]

    def get_sender_request(self, player: str) -> Optional[TeleportRequest]:
        with self._lock:
            return self._request_sender_dict.get(player)

    def get_receiver_requests(self, player: str) -> Dict[str, TeleportRequest]:
        with self._lock:
            return self._request_receiver_dict.get(player, {})


data_manager: DataManager
plugin_server: mcdr.PluginServerInterface
plugin_config: Config
save_loop: utils.LoopManager
teleport_request_manager: TeleportRequestManager
prev_data_str: str
online_player_counter: OnlinePlayerCounter


def on_load(server: mcdr.PluginServerInterface, prev_module: any):
    global \
        plugin_config, \
        data_manager, \
        plugin_server, \
        save_loop, \
        teleport_request_manager, \
        prev_data_str, \
        online_player_counter

    plugin_server = server
    plugin_config = plugin_server.load_config_simple("config.json", target_class=Config)
    simple_tp_data = plugin_server.load_config_simple(
        "data.json", target_class=SimpleTPData
    )
    # 转换旧版配置
    need_update = False
    for dim in plugin_config.worlds:
        if dim not in simple_tp_data.dimension_str2sid:
            simple_tp_data.dimension_str2sid[dim] = (
                max(simple_tp_data.dimension_str2sid.values(), default=-1) + 1
            )
            need_update = True
    if need_update:
        plugin_server.save_config_simple(simple_tp_data, "data.json")

    online_player_counter = OnlinePlayerCounter()
    if plugin_server.is_server_startup():
        online_player_counter.on_server_startup()

    data_manager = DataManager(simple_tp_data)
    prev_data_str = json.dumps(
        data_manager.get_simple_tp_data().serialize(), sort_keys=True
    )
    plugin_server.logger.debug(f"SimpleTP plugin loaded with config: {plugin_config}")

    if plugin_config.back_on_death:
        plugin_server.register_event_listener("PlayerDeathEvent", on_player_death)

    save_loop = utils.LoopManager(save_data_task, plugin_config.save_interval)
    save_loop.start()

    teleport_request_manager = TeleportRequestManager()

    need_player_kwargs = {
        "requirement": lambda src: src.is_player,
        "failure_message_getter": lambda: utils.tr("not_player_tip"),
    }

    def player_suggestion(src: mcdr.CommandSource) -> List[str]:
        return online_player_counter.get_player_list(try_query=False) or []

    def get_all_names_suggestion(src: mcdr.CommandSource) -> List[str]:
        suggestions = set()
        if src.is_player:
            assert isinstance(src, mcdr.PlayerCommandSource)
            suggestions.update(data_manager.get_personal_waypoints(src.player).keys())
        suggestions.update(data_manager.get_global_waypoints().keys())
        suggestions.update(online_player_counter.get_player_list(try_query=False) or [])
        return list(suggestions)

    plugin_server.register_command(
        mcdr.Literal(plugin_config.command_prefix)
        .then(
            mcdr.Literal(["setp", "setpersonal"])
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(
                    plugin_config.permissions.personal_waypoint
                )
            )
            .then(
                mcdr.Text("waypoint_name").runs(
                    lambda src, ctx: set_waypoint(
                        src, ctx.get("waypoint_name"), is_global=False
                    )
                )
            )
            .then(
                mcdr.Literal("-f").then(
                    mcdr.Text("waypoint_name").runs(
                        lambda src, ctx: set_waypoint(
                            src,
                            ctx.get("waypoint_name"),
                            is_global=False,
                            overwrite=True,
                        )
                    )
                )
            )
        )
        .then(
            mcdr.Literal(["setg", "setglobal"])
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(
                    plugin_config.permissions.global_waypoint
                )
            )
            .then(
                mcdr.Text("waypoint_name").runs(
                    lambda src, ctx: set_waypoint(
                        src, ctx.get("waypoint_name"), is_global=True
                    )
                )
            )
            .then(
                mcdr.Literal("-f").then(
                    mcdr.Text("waypoint_name").runs(
                        lambda src, ctx: set_waypoint(
                            src,
                            ctx.get("waypoint_name"),
                            is_global=True,
                            overwrite=True,
                        )
                    )
                )
            )
        )
        .then(
            mcdr.Literal(["tpp", "tppersonal"])
            .requires(**need_player_kwargs)
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
            mcdr.Literal(["tpg", "tpglobal"])
            .requires(**need_player_kwargs)
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
            mcdr.Literal(["delp", "delpersonal"])
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(
                    plugin_config.permissions.personal_waypoint
                )
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
            mcdr.Literal(["delg", "delglobal"])
            .precondition(
                lambda src: src.has_permission(
                    plugin_config.permissions.global_waypoint
                )
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
            mcdr.Literal(["listp", "listpersonal"])
            .requires(**need_player_kwargs)
            .runs(lambda src: src.reply(get_waypoints_messages(src, scope="personal")))
        )
        .then(
            mcdr.Literal(["listg", "listglobal"]).runs(
                lambda src: src.reply(get_waypoints_messages(src, scope="global"))
            )
        )
        .then(
            mcdr.Literal("back")
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(
                    plugin_config.permissions.personal_waypoint
                )
            )
            .runs(back_to_recorded_position)
        )
        .then(
            mcdr.Literal("tp")
            .requires(**need_player_kwargs)
            .precondition(lambda src: src.has_permission(plugin_config.permissions.tp))
            .then(
                mcdr.Text("target_player")
                .suggests(player_suggestion)
                .runs(lambda src, ctx: tp_to_player(src, ctx.get("target_player")))
            )
        )
        .then(
            mcdr.Literal("tphere")
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(plugin_config.permissions.tphere)
            )
            .then(
                mcdr.Text("target_player")
                .suggests(player_suggestion)
                .runs(lambda src, ctx: tp_here(src, ctx.get("target_player")))
            )
        )
        .then(
            mcdr.Literal("tpa")
            .requires(**need_player_kwargs)
            .precondition(lambda src: src.has_permission(plugin_config.permissions.tpa))
            .then(
                mcdr.Text("target_player")
                .suggests(player_suggestion)
                .runs(lambda src, ctx: tp_request(src, ctx.get("target_player")))
            )
        )
        .then(
            mcdr.Literal("tpahere")
            .requires(**need_player_kwargs)
            .precondition(
                lambda src: src.has_permission(plugin_config.permissions.tpahere)
            )
            .then(
                mcdr.Text("target_player")
                .suggests(player_suggestion)
                .runs(
                    lambda src, ctx: tp_request(
                        src, ctx.get("target_player"), is_reversed=True
                    )
                )
            )
        )
        .then(
            mcdr.Literal("cancel")
            .requires(**need_player_kwargs)
            .runs(lambda src: cancel_tpa_request(src))
        )
        .then(
            mcdr.Literal(["accept", "allow"])
            .requires(**need_player_kwargs)
            .runs(lambda src: deal_tp_request(src, action="accept"))
            .then(
                mcdr.Text("source_player").runs(
                    lambda src, ctx: deal_tp_request(
                        src, target_player=ctx.get("source_player"), action="accept"
                    )
                )
            )
        )
        .then(
            mcdr.Literal(["deny", "reject"])
            .requires(**need_player_kwargs)
            .runs(lambda src: deal_tp_request(src, action="deny"))
            .then(
                mcdr.Text("source_player").runs(
                    lambda src, ctx: deal_tp_request(
                        src, target_player=ctx.get("source_player"), action="deny"
                    )
                )
            )
        )
        .then(
            mcdr.Text("name")
            .precondition(lambda _: plugin_config.easy_tp)
            .requires(**need_player_kwargs)
            .suggests(get_all_names_suggestion)
            .runs(lambda src, ctx: easy_tp(src, ctx.get("name")))
        )
    )


def teleport_to_coord(
    main_body: str,
    target_coord: utils.CoordWithDimension,
    player: Optional[str] = None,
    record_back: bool = True,
) -> bool:
    if player is None:
        player = main_body
    target_dim_name = data_manager.dimension_sid2str[target_coord.dimension]

    if record_back:
        cur_position = utils.get_player_position(player)
        if cur_position is None:
            plugin_server.tell(
                player,
                mcdr.RText(
                    utils.tr(
                        "api.failed_get_position." + "you"
                        if player == main_body
                        else "other",
                        player=player,
                    ),
                    constants.ERROR_COLOR,
                ),
            )
            return False

        if not utils.teleport_check(
            main_body,
            player=player,
            player_coord=cur_position,
            target_coord=target_coord,
            check_flags=utils.TpCheckFlags.WORLD | utils.TpCheckFlags.PERMISSION,
        ):
            return False

        personal_waypoints = data_manager.get_personal_waypoints(player)
        personal_waypoints[constants.BACK_WAYPOINT_ID] = cur_position
        data_manager.set_personal_waypoints(player, personal_waypoints)
    else:
        if not utils.teleport_check(
            main_body,
            player=player,
            target_coord=target_coord,
            check_flags=utils.TpCheckFlags.WORLD | utils.TpCheckFlags.PERMISSION,
        ):
            return False

    plugin_server.execute(
        f"execute in {target_dim_name} run tp {player} {target_coord.x} {target_coord.y} {target_coord.z}"
    )
    if record_back:
        plugin_server.tell(
            player,
            mcdr.RText(
                utils.tr(
                    "tp.success_record_previous_position",
                    coord=f"{cur_position.x:.2f}, {cur_position.y:.2f}, {cur_position.z:.2f}",
                    dim=data_manager.dimension_sid2str[cur_position.dimension],
                ),
                color=constants.TIP_COLOR,
            )
            + "  "
            + utils.get_command_button(
                utils.tr("button.back.text"),
                plugin_config.command_prefix + " back",
                hover_text=utils.tr("button.back.hover"),
            ),
        )

    return True


@mcdr.new_thread("easy_tp")
def easy_tp(source: mcdr.PlayerCommandSource, name: str):
    # 优先级：个人传送点 > 全局传送点 > 在线玩家（权限足够优先tp，否则tpa）
    personal_waypoints = data_manager.get_personal_waypoints(source.player)
    if name in personal_waypoints:
        teleport_to_waypoint(source, name, is_global=False)
        return
    global_waypoints = data_manager.get_global_waypoints()
    if name in global_waypoints:
        teleport_to_waypoint(source, name, is_global=True)
        return
    player_list = online_player_counter.get_player_list()
    if player_list is None:
        source.reply(
            mcdr.RText(
                utils.tr("api.failed_get_player_list"), color=constants.ERROR_COLOR
            )
        )
        return
    target_player = utils.search_for_player(name, player_list or [])
    if target_player is None:
        source.reply(
            mcdr.RText(
                utils.tr("easy_tp.no_match", name=name),
                color=constants.ERROR_COLOR,
            )
        )
        return
    if source.has_permission(plugin_config.permissions.tp):
        tp_to_player(source, target_player)
        return
    if source.has_permission(plugin_config.permissions.tpa):
        tp_request(source, target_player)
        return
    source.reply(
        mcdr.RText(
            utils.tr("easy_tp.no_permission"),
        )
    )


@mcdr.new_thread("deal_tp_request")
def deal_tp_request(
    source: mcdr.PlayerCommandSource,
    action: Literal["accept", "deny"],
    target_player: Optional[str] = None,
):
    tp_request_dict = teleport_request_manager.get_receiver_requests(source.player)
    if not tp_request_dict:
        source.reply(
            mcdr.RText(
                utils.tr("tp_request.no_pending"),
                color=constants.ERROR_COLOR,
            )
        )
        return
    if target_player is None:
        # 使用最近的请求
        tp_request = next(reversed(tp_request_dict.values()))
    else:
        tp_request = tp_request_dict.get(target_player)
        if tp_request is None:
            source.reply(
                mcdr.RText(
                    utils.tr("tp_request.no_specific", player=target_player),
                    color=constants.ERROR_COLOR,
                )
            )
            return

    teleport_request_manager.remove_request(tp_request)
    if action == "accept":
        if not utils.teleport_check(
            source.player,
            target_player=tp_request.player,
            check_flags=utils.TpCheckFlags.ONLINE,
        ):
            return
        if tp_request.is_reversed:
            target_coord = utils.get_player_position(tp_request.player)
            if target_coord is None:
                source.reply(
                    mcdr.RText(
                        utils.tr(
                            "api.failed_get_position.other", player=tp_request.player
                        ),
                        color=constants.ERROR_COLOR,
                    )
                )
                return
        else:
            target_coord = utils.get_player_position(source.player)
            if target_coord is None:
                source.reply(
                    mcdr.RText(
                        utils.tr("api.failed_get_position.you"),
                        color=constants.ERROR_COLOR,
                    )
                )
                return
        source.reply(
            mcdr.RText(
                utils.tr("tp_request.accepted", player=tp_request.player),
                color=constants.SUCCESS_COLOR,
            )
        )
        plugin_server.tell(
            tp_request.player,
            mcdr.RText(
                utils.tr("tp_request.your_request_accepted", player=source.player),
                color=constants.SUCCESS_COLOR,
            ),
        )
        if not teleport_to_coord(
            tp_request.player,
            player=tp_request.player
            if not tp_request.is_reversed
            else tp_request.target_player,
            target_coord=target_coord,
        ):
            source.reply(
                mcdr.RText(
                    utils.tr("tp_request.failed_teleport", player=tp_request.player),
                    color=constants.ERROR_COLOR,
                )
            )
            return
    else:  # action == "deny"
        source.reply(
            mcdr.RText(
                utils.tr("tp_request.denied", player=tp_request.player),
                color=constants.SUCCESS_COLOR,
            )
        )
        plugin_server.tell(
            tp_request.player,
            mcdr.RText(
                utils.tr("tp_request.your_request_denied", player=source.player),
                color=constants.ERROR_COLOR,
            ),
        )


@mcdr.new_thread("tp_request")
def tp_request(
    source: mcdr.PlayerCommandSource,
    target_player: str,
    is_reversed: bool = False,
):
    if not utils.teleport_check(
        source.player,
        target_player=target_player,
        check_flags=utils.TpCheckFlags.ONLINE,
    ):
        return
    tp_request = TeleportRequest(
        player=source.player,
        target_player=target_player,
        timestamp=time.time(),
        is_reversed=is_reversed,
    )
    prev_request = teleport_request_manager.set_request(tp_request)
    if prev_request:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "tp_request.already_pending", player=prev_request.target_player
                ),
                color=constants.ERROR_COLOR,
            )
            + "  "
            + utils.get_command_button(
                utils.tr("button.cancel.text"),
                f"{plugin_config.command_prefix} cancel",
                hover_text=utils.tr("button.cancel.hover"),
            )
        )
        return
    source.reply(
        mcdr.RText(
            utils.tr("tp_request.request_sent", player=target_player),
            color=constants.TIP_COLOR,
        )
        + "  "
        + utils.get_command_button(
            utils.tr("button.cancel.text"),
            f"{plugin_config.command_prefix} cancel",
            hover_text=utils.tr("button.cancel.hover"),
        )
    )
    plugin_server.tell(
        target_player,
        (
            mcdr.RText(
                utils.tr("tp_request.request_received", player=source.player),
                color=constants.TIP_COLOR,
            )
            if not is_reversed
            else mcdr.RText(
                utils.tr("tp_request.request_received_reversed", player=source.player),
                color=constants.TIP_COLOR,
            )
        )
        + "  "
        + utils.get_command_button(
            utils.tr("button.accept.text"),
            f"{plugin_config.command_prefix} accept {source.player}",
            hover_text=utils.tr("button.accept.hover"),
        )
        + " "
        + utils.get_command_button(
            utils.tr("button.deny.text"),
            f"{plugin_config.command_prefix} deny {source.player}",
            hover_text=utils.tr("button.deny.hover"),
        ),
    )


@mcdr.new_thread("cancel_tpa_request")
def cancel_tpa_request(source: mcdr.PlayerCommandSource):
    tp_request = teleport_request_manager.get_sender_request(source.player)
    if tp_request is None:
        source.reply(
            mcdr.RText(
                utils.tr("tp_request.no_pending"),
                color=constants.ERROR_COLOR,
            )
        )
        return
    teleport_request_manager.remove_request(tp_request)
    source.reply(
        mcdr.RText(
            utils.tr("tp_request.cancelled", player=tp_request.target_player),
            color=constants.SUCCESS_COLOR,
        )
    )
    plugin_server.tell(
        tp_request.target_player,
        mcdr.RText(
            utils.tr("tp_request.source_cancelled", player=source.player),
            color=constants.WARNING_COLOR,
        ),
    )


@mcdr.new_thread("tp_to_user")
def tp_to_player(
    source: mcdr.PlayerCommandSource,
    target_player: str,
):
    if not target_player:
        source.reply(
            mcdr.RText(
                utils.tr("tp_user.no_target_player_provided"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if not utils.teleport_check(
        source.player,
        target_player=target_player,
        check_flags=utils.TpCheckFlags.ONLINE,
    ):
        return

    coord = utils.get_player_position(target_player)
    if coord is None:
        source.reply(
            mcdr.RText(
                utils.tr("api.failed_get_position.other", player=target_player),
                color=constants.ERROR_COLOR,
            )
        )
        return

    source.reply(
        mcdr.RText(
            utils.tr(
                "tp_user.teleporting_to",
                player=target_player,
                coord=f"{coord.x:.2f}, {coord.y:.2f}, {coord.z:.2f}",
                dim=data_manager.dimension_sid2str[coord.dimension],
            ),
            color=constants.SUCCESS_COLOR,
        )
    )
    teleport_to_coord(source.player, target_coord=coord)


@mcdr.new_thread("tphere")
def tp_here(
    source: mcdr.PlayerCommandSource,
    target_player: str,
):
    if not target_player:
        source.reply(
            mcdr.RText(
                utils.tr("tp_here.no_target_player_provided"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    coord = utils.get_player_position(source.player)
    if coord is None:
        source.reply(
            mcdr.RText(
                utils.tr("api.failed_get_position.you"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if not utils.teleport_check(
        source.player,
        target_player=target_player,
        check_flags=utils.TpCheckFlags.ONLINE,
    ):
        return

    source.reply(
        mcdr.RText(
            utils.tr("tp_here.teleporting_player", player=target_player),
            color=constants.SUCCESS_COLOR,
        )
    )
    plugin_server.tell(
        target_player,
        mcdr.RText(utils.tr("tp_here.being_teleported", player=source.player)),
    )
    teleport_to_coord(source.player, target_coord=coord, player=target_player)


@mcdr.new_thread("delete_waypoint")
def delete_waypoint(
    source: mcdr.CommandSource,
    waypoint_name: str,
    is_global: bool,
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                utils.tr("waypoint.del.no_name_provided"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.del.back_reserved", back_id=constants.BACK_WAYPOINT_ID
                ),
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
                    utils.tr("not_player_tip"),
                    color=constants.ERROR_COLOR,
                )
            )
            return
        assert isinstance(source, mcdr.PlayerCommandSource)
        waypoint_dict = data_manager.get_personal_waypoints(source.player)

    if waypoint_name not in waypoint_dict:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.del.not_found." + ("global" if is_global else "personal"),
                    name=waypoint_name,
                ),
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
            utils.tr(
                "waypoint.del.success." + ("global" if is_global else "personal"),
                name=waypoint_name,
            ),
            color=constants.SUCCESS_COLOR,
        )
    )


@mcdr.new_thread("teleport_to_waypoint")
def teleport_to_waypoint(
    source: mcdr.PlayerCommandSource, waypoint_name: str, is_global: bool
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                utils.tr("waypoint.tp.no_name_provided"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.tp.back_reserved", back_id=constants.BACK_WAYPOINT_ID
                ),
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
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.tp.not_found." + ("global" if is_global else "personal"),
                    name=waypoint_name,
                ),
                color=constants.ERROR_COLOR,
            )
        )
        return

    position = waypoint_dict[waypoint_name]
    source.reply(
        mcdr.RText(
            utils.tr(
                "waypoint.tp.teleporting",
                name=waypoint_name,
                dim=data_manager.dimension_sid2str[position.dimension],
                coord=f"{position.x:.2f}, {position.y:.2f}, {position.z:.2f}",
            ),
            color=constants.SUCCESS_COLOR,
        )
    )
    teleport_to_coord(source.player, target_coord=position)


@mcdr.new_thread("create_waypoint")
def set_waypoint(
    source: mcdr.PlayerCommandSource,
    waypoint_name: str,
    is_global: bool,
    overwrite: bool = False,
):
    if not waypoint_name:
        source.reply(
            mcdr.RText(
                utils.tr("waypoint.set.no_name_provided"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if waypoint_name == constants.BACK_WAYPOINT_ID:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.set.back_reserved", back_id=constants.BACK_WAYPOINT_ID
                ),
                color=constants.ERROR_COLOR,
            )
        )
        return

    player = source.player
    position = utils.get_player_position(player)
    if position is None:
        source.reply(
            mcdr.RText(
                utils.tr("api.failed_get_position.you"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if data_manager.dimension_sid2str[position.dimension] not in plugin_config.worlds:
        source.reply(
            mcdr.RText(
                utils.tr(
                    "config.dim_not_allowed.you",
                    dim=data_manager.dimension_sid2str[position.dimension],
                ),
                color=constants.ERROR_COLOR,
            )
        )
        return

    if is_global:
        waypoint_dict = data_manager.get_global_waypoints()
    else:
        waypoint_dict = data_manager.get_personal_waypoints(player)
    if waypoint_name in waypoint_dict:
        old_position = waypoint_dict[waypoint_name]
        if not overwrite:
            source.reply(
                mcdr.RText(
                    utils.tr(
                        "waypoint.set.exists",
                        name=waypoint_name,
                        dim=data_manager.dimension_sid2str[old_position.dimension],
                        coord=f"{old_position.x:.2f}, {old_position.y:.2f}, {old_position.z:.2f}",
                    ),
                    color=constants.ERROR_COLOR,
                )
            )
            return
        source.reply(
            mcdr.RText(
                utils.tr(
                    "waypoint.set.overwrite",
                    name=waypoint_name,
                    dim=data_manager.dimension_sid2str[old_position.dimension],
                    coord=f"{old_position.x:.2f}, {old_position.y:.2f}, {old_position.z:.2f}",
                ),
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
            utils.tr(
                "waypoint.set.success",
                name=waypoint_name,
                dim=data_manager.dimension_sid2str[position.dimension],
                coord=f"{position.x:.2f}, {position.y:.2f}, {position.z:.2f}",
            ),
            color=constants.SUCCESS_COLOR,
        )
    )


@mcdr.new_thread("back_to_recorded_position")
def back_to_recorded_position(source: mcdr.PlayerCommandSource):
    player = source.player
    personal_waypoints = data_manager.get_personal_waypoints(player)
    if constants.BACK_WAYPOINT_ID not in personal_waypoints:
        source.reply(
            mcdr.RText(
                utils.tr("back.no_recorded_position"),
                color=constants.ERROR_COLOR,
            )
        )
        return

    position = personal_waypoints[constants.BACK_WAYPOINT_ID]
    source.reply(
        mcdr.RText(
            utils.tr(
                "back.teleporting",
                dim=data_manager.dimension_sid2str[position.dimension],
                coord=f"{position.x:.2f}, {position.y:.2f}, {position.z:.2f}",
            ),
            color=constants.SUCCESS_COLOR,
        )
    )
    teleport_to_coord(source.player, target_coord=position)


def get_waypoints_messages(
    source: mcdr.CommandSource, scope: Literal["personal", "global", "all"] = "all"
) -> mcdr.RText:
    def get_dim_color(dim_sid: int) -> mcdr.RColor:
        dim_name = data_manager.dimension_sid2str[dim_sid]
        index = (
            plugin_config.worlds.index(dim_name)
            if dim_name in plugin_config.worlds
            else -1
        )
        if index >= len(constants.DIM_COLORS):
            return constants.DIM_COLORS[-1]
        return constants.DIM_COLORS[index]

    def waypoint_item_to_rtext(
        name: str, pos: utils.CoordWithDimension, is_global: bool
    ) -> mcdr.RText:
        rtext = mcdr.RText(name, color=get_dim_color(pos.dimension)) + mcdr.RText(
            f": {data_manager.dimension_sid2str[pos.dimension]}({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})",
            color=mcdr.RColor.gray,
        )
        if source.is_player:
            rtext += "  " + utils.get_command_button(
                utils.tr("button.tp.text"),
                f"{plugin_config.command_prefix} tpg {name}"
                if is_global
                else f"{plugin_config.command_prefix} tpp {name}",
            )
            if not is_global or source.has_permission(
                plugin_config.permissions.global_waypoint
            ):
                rtext += " " + utils.get_command_button(
                    utils.tr("button.del.text"),
                    f"{plugin_config.command_prefix} delg {name}"
                    if is_global
                    else f"{plugin_config.command_prefix} delp {name}",
                    hover_text=utils.tr("button.del.hover"),
                    color=mcdr.RColor.red,
                )
        return rtext

    replyTextLines: List[mcdr.RText] = []
    if source.is_player and scope != "global":
        assert isinstance(source, mcdr.PlayerCommandSource)
        replyTextLines.append(
            mcdr.RText(
                utils.tr("list.personal_waypoints_header"),
                color=mcdr.RColor.light_purple,
            )
        )
        waypoints = data_manager.get_personal_waypoints(source.player)
        if not waypoints:
            replyTextLines.append(
                mcdr.RText(
                    utils.tr("list.no_personal_waypoints"), color=mcdr.RColor.gray
                )
            )
        for name, pos in waypoints.items():
            if name == constants.BACK_WAYPOINT_ID:
                continue
            replyTextLines.append(waypoint_item_to_rtext(name, pos, is_global=False))

    if scope != "personal":
        replyTextLines.append(
            mcdr.RText(
                utils.tr("list.global_waypoints_header"), color=mcdr.RColor.light_purple
            )
        )
        waypoints = data_manager.get_global_waypoints()
        if not waypoints:
            replyTextLines.append(
                mcdr.RText(utils.tr("list.no_global_waypoints"), color=mcdr.RColor.gray)
            )
        for name, pos in waypoints.items():
            replyTextLines.append(waypoint_item_to_rtext(name, pos, is_global=True))

    return mcdr.RTextBase.join("\n", replyTextLines)


@mcdr.new_thread("on_player_death")
def on_player_death(server: mcdr.PluginServerInterface, player: str, event: str, _):
    death_position = utils.get_player_position(player)
    if death_position is None:
        server.tell(
            player,
            mcdr.RText(
                utils.tr("api.failed_get_position.you"),
                constants.ERROR_COLOR,
            ),
        )
        return

    if death_position.dimension not in data_manager.dimension_sid2str:
        server.tell(
            player,
            mcdr.RText(
                utils.tr(
                    "back.recorded_on_death.failed_dim",
                    dim=data_manager.dimension_sid2str[death_position.dimension],
                ),
                constants.ERROR_COLOR,
            ),
        )
        return

    personal_waypoints = data_manager.get_personal_waypoints(player)
    personal_waypoints[constants.BACK_WAYPOINT_ID] = utils.CoordWithDimension(
        death_position.x, death_position.y, death_position.z, death_position.dimension
    )
    data_manager.set_personal_waypoints(player, personal_waypoints)
    server.tell(
        player,
        mcdr.RText(
            utils.tr(
                "back.recorded_on_death.success",
                coord=f"{death_position.x:.2f}, {death_position.y:.2f}, {death_position.z:.2f}",
                dim=data_manager.dimension_sid2str[death_position.dimension],
            ),
            color=constants.TIP_COLOR,
        )
        + "  "
        + utils.get_command_button(
            utils.tr("button.death_back.text"),
            plugin_config.command_prefix + " back",
            hover_text=utils.tr("button.death_back.hover"),
        ),
    )


def save_data_task():
    global prev_data_str
    cur_data = data_manager.get_simple_tp_data()
    cur_data_str = json.dumps(cur_data.serialize(), sort_keys=True)
    if cur_data_str == prev_data_str:
        plugin_server.logger.debug(
            "No changes detected in SimpleTP data, skipping save."
        )
        return
    plugin_server.logger.debug("Performing scheduled save of SimpleTP data.")
    plugin_server.save_config_simple(cur_data, "data.json")
    prev_data_str = cur_data_str


def on_unload(server: mcdr.PluginServerInterface):
    save_loop.stop()
    plugin_server.logger.info("Saving SimpleTP data on unload.")
    save_data_task()


def on_player_joined(server: mcdr.PluginServerInterface, player: str, info: mcdr.Info):
    online_player_counter.on_player_joined(player)


def on_player_left(server: mcdr.PluginServerInterface, player: str):
    online_player_counter.on_player_left(player)


def on_server_startup(server: mcdr.PluginServerInterface):
    online_player_counter.on_server_startup()