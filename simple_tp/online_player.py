import threading
from typing import List, Optional, Set
import minecraft_data_api as mc_data_api
from readerwriterlock.rwlock import RWLockFair
import simple_tp


class OnlinePlayerCounter:
    def __init__(self):
        self._players: Optional[Set[str]] = None
        self.lock = RWLockFair()

    def query_players(self, rewrite: bool = False):
        try:
            with self.lock.gen_wlock():
                # 防止重复查询
                if self._players is not None and not rewrite:
                    return
                player_list = mc_data_api.get_server_player_list().players
                self._players = set(player_list)
                simple_tp.plugin_server.logger.info(
                    f"Queried online players successfully: {player_list}"
                )
        except Exception as e:
            simple_tp.plugin_server.logger.error(f"Error getting player list: {e}")

    def on_server_startup(self):
        threading.Thread(
            target=self.query_players,
            kwargs={"rewrite": True},
            daemon=True,
            name="OnlinePlayersInit",
        ).start()

    def get_player_list(self, try_query: bool = True) -> Optional[List[str]]:
        with self.lock.gen_rlock():
            if self._players is not None:
                return list(self._players)
        if not try_query:
            return None
        self.query_players()
        with self.lock.gen_rlock():
            if self._players is not None:
                return list(self._players)
        return None

    def on_player_joined(self, player: str):
        with self.lock.gen_wlock():
            if self._players is None:
                return
            if player in self._players:
                simple_tp.plugin_server.logger.warning(
                    f"Player {player} already in online players set when joining, data may be inconsistent, refreshing..."
                )
                threading.Thread(
                    target=self.query_players,
                    kwargs={"rewrite": True},
                    daemon=True,
                    name="OnlinePlayersRefresh",
                ).start()
                return
            self._players.add(player)

    def on_player_left(self, player: str):
        with self.lock.gen_wlock():
            if self._players is None:
                return
            try:
                self._players.remove(player)
            except KeyError:
                simple_tp.plugin_server.logger.warning(
                    f"Player {player} not in online players set when leaving, data may be inconsistent, refreshing..."
                )
                threading.Thread(
                    target=self.query_players,
                    kwargs={"rewrite": True},
                    daemon=True,
                    name="OnlinePlayersRefresh",
                ).start()
