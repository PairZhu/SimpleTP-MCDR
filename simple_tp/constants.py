from mcdreforged.api.rtext import RColor

BACK_WAYPOINT_ID = "__back__"

SUCCESS_COLOR = RColor.green
WARNING_COLOR = RColor.yellow
ERROR_COLOR = RColor.red
TIP_COLOR = RColor.light_purple

DIM_ID2STR = {
    0: "minecraft:overworld",
    -1: "minecraft:the_nether",
    1: "minecraft:the_end",
}

DIM_COLORS = [
    RColor.green,  # Overworld
    RColor.dark_red,  # Nether
    RColor.light_purple,  # End
    RColor.gold,  # Custom dimensions
]
