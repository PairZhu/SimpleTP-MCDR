from typing import Dict, List, Union, Optional
from readerwriterlock.rwlock import RWLockFair

import mcdreforged.api.all as mcdr

from simple_tp.utils import CoordWithDimension


class SimpleTPData(mcdr.Serializable):
    personal_waypoints: Dict[str, Dict[str, List[Union[float, int]]]] = {}
    global_waypoints: Dict[str, List[Union[float, int]]] = {}
    dimension_str2sid: Dict[str, int] = {}


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
        self.dimension_str2sid = data.dimension_str2sid
        self.dimension_sid2str = {v: k for k, v in self.dimension_str2sid.items()}
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
        data.dimension_str2sid = self.dimension_str2sid
        return data
