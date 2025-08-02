from typing import List

import mcdreforged.api.all as mcdr


class Config(mcdr.Serializable):
    command_prefix: str = "!!stp"
    back_on_death: bool = True
    save_interval: int = 30  # seconds

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
