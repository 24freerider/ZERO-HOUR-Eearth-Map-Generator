"""Battle Royale radiation geometry, phase scripts and elite drops."""
import math

import numpy as np

import geo
from g_brscript import ARENA, CLOCK, DROPS, PREFIX, STORM, WARNING, ZONE, message, radar_ping, spawn
from g_invasion import counter, expired, set_counter, timer
from mapio import Script

FIELD = 'RadiationFieldLarge'
ZONE_MARKER = 'EARTH_BR_RadarMarker'
ELITE_DROP = 'EARTH_BR_EliteDrop'
ZONE_PING_TIMERS = {f'{PREFIX}ZonePing{seconds}': seconds for seconds in (2, 4, 6)}


class BattleRoyaleZoneMixin:
    def br_zones(self, ground):
        # The radii cover every field tile from its centre to its furthest corner.
        pitch = max(85.0, math.sqrt(self.AX * self.AY / 440), max(self.AX, self.AY) / 46)
        nx, ny = max(4, round(self.AX / pitch)), max(4, round(self.AY / pitch))
        dx, dy = self.AX / nx, self.AY / ny
        half_diagonal = math.hypot(dx, dy) / 2
        self.br_field_radius = math.ceil(half_diagonal * 1.07) + 2
        center = np.array((self.AX / 2, self.AY / 2))
        interior = ground[(np.abs((ground - center) / center) <= .42).all(axis=1)]
        if not len(interior):
            raise ValueError('Battle Royale: no dry open ground in the central area for a final safe zone.')
        # Pick distinct central grid cells that contain open ground.
        cells = sorted({(int(x / dx), int(y / dy)) for x, y in interior})
        choices = self.rng.sample(cells, min(self.opt['br_zone_choices'], len(cells)))
        self.br_zone_patterns = []
        self.br_zone_markers = {}
        drop_ground = self.br_points(5, dense=True)
        self.br_airdrops = {ARENA: self.br_airdrop_sites(ARENA, drop_ground if len(drop_ground) else ground)}
        self.br_waypoint(PREFIX + 'View', *center)
        fields = {}
        for iy in range(ny):
            for ix in range(nx):
                name = f'{PREFIX}Field{ix}_{iy}'
                fields[name] = ((ix + .5) * dx, (iy + .5) * dy)
                self.br_waypoint(name, *fields[name])
        final = 1.95 * half_diagonal * self.opt['br_zone_final_size'] / 100
        first = max(.48 * min(self.AX, self.AY), 1.65 * final)
        beacons = self.opt['br_zone_beacons']
        drop_count = self.opt['br_drop_count']
        for k, (ix, iy) in enumerate(choices):
            x, y = (ix + .5) * dx, (iy + .5) * dy
            previous = set(fields)
            pattern = []
            for i in range(len(self.br_phases)):
                radius = first * (final / first) ** (i / (len(self.br_phases) - 1))
                name = f'{PREFIX}Safe{k}_{i + 1}'
                self.br_waypoint(name + '_Warning', x, y)
                rim = [(x + radius * math.cos(math.tau * j / 120),
                        y + radius * math.sin(math.tau * j / 120)) for j in range(120)]
                self.area_triggers.append((name, geo.clip_poly(rim[::4], self.AX, self.AY)))
                visible = [point for point in rim if self.inside(*point, 1)]
                markers = []
                if visible:
                    for j in range(beacons):
                        marker = f'{name}_Beacon{j}'
                        self.br_waypoint(marker, *visible[j * len(visible) // beacons])
                        markers.append(marker)
                self.br_zone_markers[name] = markers
                # A tile that reaches beyond the circle must radiate, even if its centre is inside.
                newly_unsafe = sorted(field for field in previous
                                      if math.dist((x, y), fields[field]) + half_diagonal >= radius)
                previous.difference_update(newly_unsafe)
                distance = ((drop_ground - (x, y)) ** 2).sum(axis=1)
                candidates = drop_ground[distance < (radius - 12) ** 2]
                if len(candidates) < drop_count:
                    inside = ground[((ground - (x, y)) ** 2).sum(axis=1) < (radius - 5) ** 2]
                    candidates = np.unique(np.concatenate((candidates, inside)), axis=0)
                clear = np.ones(len(candidates), dtype=bool)
                for field, point in fields.items():
                    if field not in previous and math.dist((x, y), point) < radius + self.br_field_radius + 8:
                        clear &= ((candidates - point) ** 2).sum(axis=1) > (self.br_field_radius + 8) ** 2
                if clear.sum() >= drop_count:
                    candidates = candidates[clear]
                self.br_airdrops[name] = self.br_airdrop_sites(name, candidates)
                pattern.append((name, newly_unsafe, (x, y, radius)))
            self.br_zone_patterns.append(pattern)

    def br_airdrop_sites(self, area, candidates):
        sites = []
        if len(candidates) and self.opt['br_drop_count']:
            # Keep crate locations separated even in the final circle.
            distances = np.full(len(candidates), np.inf)
            idx = self.rng.randrange(len(candidates))
            for i in range(min(self.opt['br_drop_count'], len(candidates))):
                name = f'{area}_AirDrop{i}'
                x, y = candidates[idx]
                self.br_waypoint(name, x, y)
                sites.append(name)
                np.minimum(distances, ((candidates - (x, y)) ** 2).sum(axis=1), out=distances)
                idx = distances.argmax()
        return sites

    def br_airdrop_actions(self, area):
        actions = []
        if self.br_airdrops[area]:
            actions += [('PLAY_SOUND_EFFECT', [('sound', 'ScriptJetFlyBy1')]), message('AirDrop')]
        for name in self.br_airdrops[area]:
            actions += [spawn(ELITE_DROP, 'team' + DROPS, name, name), radar_ping(name),
                        ('ENABLE_SCRIPT', [('script', name + '_Beacon')])]
        return actions

    def br_airdrop_beacons(self, area):
        return [Script(name + '_Beacon', [radar_ping(name)],
                       conditions=[('NAMED_NOT_DESTROYED', [('unit', name)])], once=False, delay=4, active=False,
                       else_actions=[('DISABLE_SCRIPT', [('script', name + '_Beacon')])])
            for name in self.br_airdrops[area]]

    def br_zone_scripts(self, pick, phases, warning_lead, first_warning_lead):
        scripts = []
        for k, pattern in enumerate(self.br_zone_patterns):
            for i, (area, fields, _) in enumerate(pattern):
                warning = area + '_Warning'
                warning_conditions = [counter(pick, 2, k), counter(ZONE, 2, i), expired(WARNING)]
                warning_ping = radar_ping(warning)
                if i == 0 or warning_lead:
                    if i == 0:
                        notice = [('PLAY_SOUND_EFFECT', [('sound', 'AirRaidSiren')]),
                                  ('SHOW_MILITARY_CAPTION', [('text', 'SCRIPT:' + PREFIX + 'FirstRadiationBriefing'),
                                                            ('int', min(10, first_warning_lead) * 1000)])]
                        timer_label = 'FirstRadiationTimer'
                    else:
                        notice = [message('ZoneWarning')]
                        timer_label = 'ZoneWarningTimer'
                    scripts.append(Script(warning, [
                        *notice,
                        ('DISPLAY_COUNTDOWN_TIMER', [('counter', CLOCK), ('text', 'SCRIPT:' + PREFIX + timer_label)]),
                        spawn(ZONE_MARKER, 'team' + STORM, warning, warning),
                        warning_ping,
                    ], conditions=warning_conditions))
                    scripts.append(Script(warning + '_Beacon', [warning_ping], once=False, delay=3,
                                          conditions=warning_conditions + [('NAMED_NOT_DESTROYED', [('unit', warning)])]))
                actions = [set_counter(ZONE, i + 1), message('Closing' if i + 1 < len(phases) else 'Final'),
                           ('PLAY_SOUND_EFFECT', [('sound', 'PreDirtyNukeDetonation')])]
                if i + 1 < len(phases):
                    actions += [timer(CLOCK, phases[i + 1]), timer(WARNING, max(0, phases[i + 1] - warning_lead)),
                                ('DISPLAY_COUNTDOWN_TIMER', [('counter', CLOCK), ('text', 'SCRIPT:' + PREFIX + 'Next')])]
                else:
                    actions.append(('HIDE_COUNTDOWN_TIMER', [('counter', CLOCK)]))
                actions += [spawn(FIELD, 'team' + STORM, field) for field in fields]
                actions += self.br_airdrop_actions(area)
                markers = self.br_zone_markers[area]
                pings = [radar_ping(marker) for marker in markers]
                actions += [spawn(ZONE_MARKER, 'team' + STORM, marker, marker) for marker in markers]
                actions += pings
                actions += [timer(name, seconds) for name, seconds in ZONE_PING_TIMERS.items()]
                scripts.append(Script(f'{area}_Activate', actions,
                                      conditions=[counter(pick, 2, k), counter(ZONE, 2, i), expired(CLOCK)]))
                scripts.extend(self.br_airdrop_beacons(area))
                for name, seconds in ZONE_PING_TIMERS.items():
                    scripts.append(Script(f'{area}_Ping{seconds}', pings,
                                          conditions=[counter(pick, 2, k), counter(ZONE, 2, i + 1), expired(name)]))
        return scripts
