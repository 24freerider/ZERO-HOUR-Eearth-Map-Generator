"""Shared script names and engine actions for Battle Royale components."""

PREFIX = 'EARTH_BR_'
NEUTRAL = 'EarthBRNeutral'
STORM = 'EarthBRStorm'
DROPS = 'EarthBRAirDrops'
ARENA = PREFIX + 'Arena'
ZONE = PREFIX + 'Zone'
CLOCK = PREFIX + 'Clock'
WARNING = PREFIX + 'ZoneWarning'
RADAR_BEACON = 5
PEOPLE = 'EarthBRCivilians'


def spawn(model, team, waypoint, name=None):
    args = [('type', model), ('team', team), ('waypoint', waypoint)]
    if name is not None:
        return ('CREATE_NAMED_ON_TEAM_AT_WAYPOINT', [('unit', name)] + args)
    return ('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT', args)


def transfer(name, player):
    return ('NAMED_TRANSFER_OWNERSHIP_PLAYER', [('unit', name), ('side', player)])


def message(key):
    return ('DISPLAY_TEXT', [('text', f'SCRIPT:{PREFIX}{key}')])


def radar_ping(name):
    return ('OBJECT_CREATE_RADAR_EVENT', [('unit', name), ('radar_event', RADAR_BEACON)])
