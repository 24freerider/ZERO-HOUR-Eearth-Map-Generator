"""Editable Battle Royale defaults shared by the generator and the UI."""

PHASES = (300, 110, 95, 80, 65, 50, 35, 25)
FIRST_RADIATION_WARNING = 20
BR_COUNTS = {
    'br_civilian_count': (160, 2000, 'Počet civilistov'),
    'br_rebel_count': (500, 1200, 'Počet rebelov'),
    'br_vehicle_count': (280, 500, 'Počet civilných vozidiel'),
}

# default, minimum, maximum, Slovak label, English label
BR_GROUPS = {
    'Hlavná postava': {
        'br_start_money': (0, 0, 100000, 'Štartovacie peniaze ($)', 'Starting cash ($)'),
        'br_hp_usa': (720, 1, 10000, 'Ranger – zdravie', 'Ranger health'),
        'br_hp_china': (480, 1, 10000, 'Red Guard – zdravie', 'Red Guard health'),
        'br_hp_gla': (480, 1, 10000, 'Rebel – zdravie', 'Rebel health'),
        'br_move_usa': (20, 5, 120, 'Ranger – rýchlosť', 'Ranger speed'),
        'br_move_china': (25, 5, 120, 'Red Guard – rýchlosť', 'Red Guard speed'),
        'br_move_gla': (20, 5, 120, 'Rebel – rýchlosť', 'Rebel speed'),
        'br_heal_delay': (3000, 0, 60000, 'Liečenie po zásahu (ms)', 'Healing delay after damage (ms)'),
        'br_heal_tick': (500, 100, 10000, 'Interval liečenia (ms)', 'Healing interval (ms)'),
        'br_heal_amount': (8, 0, 200, 'Zdravie za interval', 'Health per interval'),
        'br_rifle_usa': (5, 1, 1000, 'Puška – poškodenie Rangera', 'Ranger rifle damage'),
        'br_rifle_china': (15, 1, 1000, 'Puška – poškodenie Red Guarda', 'Red Guard rifle damage'),
        'br_rifle_gla': (5, 1, 1000, 'Puška – poškodenie Rebela', 'Rebel rifle damage'),
        'br_rifle_range': (100, 25, 500, 'Dosah pušiek', 'Rifle range'),
        'br_rifle_interval': (300, 50, 10000, 'Ranger / Rebel – medzi výstrelmi (ms)', 'Ranger / Rebel shot interval (ms)'),
        'br_redguard_interval': (1000, 50, 10000, 'Red Guard – medzi výstrelmi (ms)', 'Red Guard shot interval (ms)'),
    },
    'Trvalé vylepšenia': {
        'br_price_heal': (100, 0, 100000, 'Rýchle liečenie ($)', 'Fast healing price ($)'),
        'br_fast_heal': (12, 0, 200, 'Bonus zdravia za interval', 'Bonus health per interval'),
        'br_price_armor': (200, 0, 100000, 'Vesta ($)', 'Body armor price ($)'),
        'br_armor_hp': (200, 0, 10000, 'Vesta – pridané zdravie', 'Body armor extra health'),
        'br_price_speed': (300, 0, 100000, 'Adrenalín ($)', 'Adrenaline price ($)'),
        'br_speed_upgraded': (36, 5, 150, 'Rýchlosť po kúpe adrenalínu', 'Speed with adrenaline'),
        'br_price_stealth': (400, 0, 100000, 'Maskovanie ($)', 'Stealth price ($)'),
        'br_stealth_delay': (2000, 0, 60000, 'Maskovanie po zastavení (ms)', 'Stealth delay after stopping (ms)'),
        'br_price_damage': (400, 0, 100000, 'Lepšia zbraň ($)', 'Better weapon price ($)'),
        'br_damage_bonus': (100, 0, 500, 'Bonus k poškodeniu pušky (%)', 'Bonus rifle damage (%)'),
        'br_price_capture': (150, 0, 100000, 'Obsadzovanie budov ($)', 'Capture buildings price ($)'),
    },
    'Schopnosti a výzbroj': {
        'br_enable_rpg': (True, 0, 1, 'RPG', 'RPG'),
        'br_price_rpg': (350, 0, 100000, 'RPG – cena ($)', 'RPG price ($)'),
        'br_rpg_damage': (40, 1, 3000, 'RPG – poškodenie', 'RPG damage'),
        'br_rpg_range': (175, 10, 800, 'RPG – dosah', 'RPG range'),
        'br_rpg_interval': (1000, 100, 30000, 'RPG – medzi výstrelmi (ms)', 'RPG shot interval (ms)'),
        'br_enable_sniper': (True, 0, 1, 'Ostreľovacia puška', 'Sniper rifle'),
        'br_price_sniper': (500, 0, 100000, 'Ostreľovacia puška – cena ($)', 'Sniper rifle price ($)'),
        'br_sniper_damage': (100, 1, 300, 'Ostreľovacia puška – poškodenie', 'Sniper rifle damage'),
        'br_sniper_range': (245, 25, 700, 'Ostreľovacia puška – dosah', 'Sniper rifle range'),
        'br_sniper_interval': (3100, 500, 10000, 'Ostreľovacia puška – medzi výstrelmi (ms)', 'Sniper rifle shot interval (ms)'),
        'br_enable_c4': (True, 0, 1, 'Diaľková C4', 'Remote C4'),
        'br_price_c4': (200, 0, 100000, 'Cena jednej C4 ($)', 'Price per C4 charge ($)'),
        'br_c4_limit': (5, 1, 5, 'Max. položených náloží naraz', 'Max. planted charges at once'),
        'br_c4_damage': (2000, 1, 20000, 'C4 – priame poškodenie', 'C4 direct damage'),
        'br_c4_radius': (25, 1, 300, 'C4 – vnútorný polomer', 'C4 inner blast radius'),
        'br_c4_splash_damage': (150, 0, 20000, 'C4 – plošné poškodenie', 'C4 splash damage'),
        'br_c4_splash_radius': (75, 1, 300, 'C4 – vonkajší polomer', 'C4 outer blast radius'),
        'br_c4_plant': (1100, 100, 10000, 'C4 – čas pokladania (ms)', 'C4 placement time (ms)'),
        'br_enable_air': (True, 0, 1, 'Precision Airstrike', 'Precision Airstrike'),
        'br_price_air': (2000, 0, 100000, 'Precision – cena ($)', 'Precision price ($)'),
        'br_air_damage': (200, 1, 5000, 'Precision – poškodenie rakety', 'Precision damage per missile'),
        'br_air_radius': (50, 1, 400, 'Precision – polomer rakety', 'Precision missile radius'),
        'br_enable_cluster': (True, 0, 1, 'Cluster Strike', 'Cluster Strike'),
        'br_price_clusterbuy': (2500, 0, 100000, 'Cluster – cena ($)', 'Cluster price ($)'),
        'br_cluster_damage': (135, 1, 5000, 'Cluster – poškodenie bomby', 'Cluster damage per bomb'),
        'br_cluster_radius': (45, 1, 400, 'Cluster – polomer bomby', 'Cluster bomb radius'),
        'br_cluster_delay': (2100, 250, 15000, 'Cluster – čas do prvého zásahu (ms)', 'Cluster time to first impact (ms)'),
        'br_enable_drone': (True, 0, 1, 'Prieskumný dron', 'Scout drone'),
        'br_price_drone': (600, 0, 100000, 'Dron – cena ($)', 'Drone price ($)'),
        'br_drone_detect': (240, 25, 1500, 'Dron – dosah detekcie', 'Drone detection range'),
        'br_drone_vision': (500, 25, 2500, 'Dron – odkrytie mapy', 'Drone shroud-clearing range'),
    },
    'Zóna': {
        'br_zone_phases': (8, 2, 8, 'Počet zúžení', 'Number of shrinks'),
        **{f'br_zone_phase{i}': (seconds, 2, 1800, f'Čas do zúženia {i} (s)', f'Time until shrink {i} (s)')
           for i, seconds in enumerate(PHASES, 1)},
        'br_zone_warning': (10, 0, 120, 'Varovanie pred ďalšími zúženiami (s, 0 = vyp.)', 'Later shrink warnings (s, 0 = off)'),
        'br_zone_beacons': (10, 0, 12, 'Beacony po obvode', 'Perimeter beacons'),
        'br_zone_choices': (3, 1, 3, 'Varianty finálneho stredu', 'Possible final centers'),
        'br_zone_final_size': (100, 75, 250, 'Veľkosť finálnej zóny (%)', 'Final safe zone size (%)'),
        'br_zone_damage': (75, 0, 3000, 'Radiácia – poškodenie', 'Radiation damage'),
        'br_zone_tick': (1000, 250, 10000, 'Radiácia – interval (ms)', 'Radiation damage interval (ms)'),
        'br_zone_building_pct': (20, 0, 100, 'Radiácia na budovy (%)', 'Radiation applied to buildings (%)'),
    },
    'Mapa a odmeny': {
        **{key: (default, 0, limit, sk, en)
           for (key, (default, limit, sk)), en in zip(BR_COUNTS.items(), ('Civilians', 'Rebels', 'Civilian vehicles'))},
        'br_humvee_count': (64, 0, 64, 'Počet Humvee', 'Humvees'),
        'br_nuke_truck_count': (3, 0, 8, 'Počet Nuke Truckov', 'Nuke trucks'),
        'br_vehicle_price': (1000, 0, 100000, 'Civilné auto – trvalá kúpa ($)', 'Civilian car ownership price ($)'),
        'br_vehicle_scout_range': (250, 70, 1000, 'Civilné auto – dosah prieskumu', 'Civilian car scout range'),
        'br_rebel_range': (125, 35, 250, 'Dosah videnia rebelov', 'Rebel vision range'),
        'br_civilian_reward': (10, 0, 10000, 'Odmena za civilistu ($)', 'Civilian reward ($)'),
        'br_farmer_reward': (100, 0, 10000, 'Odmena za farmára ($)', 'Farmer reward ($)'),
        'br_rebel_reward': (50, 0, 10000, 'Odmena za rebela ($)', 'Rebel reward ($)'),
        'br_drop_count': (3, 0, 5, 'Elitné debny na fázu', 'Elite crates per phase'),
        'br_drop_time': (30, 0, 1800, 'Prvá vlna debien (s)', 'First crate wave (s)'),
        'br_drop_xp': (120, 1, 10000, 'Bojové XP na jednu hodnosť', 'Combat XP per rank'),
    },
}
BR_SETTINGS = {key: spec for group in BR_GROUPS.values() for key, spec in group.items()}


def migrate_br_settings(settings):
    # Old saves stored the old defaults as explicit values. Keep customized balances.
    if ('br_vehicle_price' not in settings and settings.get('br_sniper_damage') == 32
            and settings.get('br_sniper_interval') == 1550):
        return {**settings, 'br_sniper_damage': BR_SETTINGS['br_sniper_damage'][0],
                'br_sniper_interval': BR_SETTINGS['br_sniper_interval'][0]}
    return settings
