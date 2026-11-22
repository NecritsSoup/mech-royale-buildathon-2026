"""First-order combat timing from the official weapon configuration."""
import math


# A hundredth of observed charge and a thousandth of a stat can each round
# toward the other side of the comparison. Include both half-unit errors.
CHARGE_ROUNDING = .01/2+.001/2+1e-9


def laser_ready(weapon):
    return weapon.get('charge', 0) > 0 and weapon.get('charge', 0)+CHARGE_ROUNDING >= weapon['stats']['charge']


def enemy_limits(config):
    """Conservative bounds using the official upgrade effects and stack caps."""
    limits = {'armor':config['mech'].get('armor', 0),
              'regen_per_s':config['mech'].get('regen_per_s', 45),
              'boost_speed':config['mech'].get('boost_speed', 19.5)}
    for upgrade in config.get('upgrades', []):
        for _ in range(int(upgrade.get('max', 1))):
            for effect in upgrade.get('effects', []):
                key = effect['path'].removeprefix('mech.')
                if effect['path'].startswith('mech.') and key in limits:
                    value = limits[key]*effect.get('mul', 1)+effect.get('add', 0)
                    limits[key] = max(limits[key], value)
    return limits


def laser_finishes(weapon, me, target, config, limits):
    """Only release early when a deliberately conservative beam is lethal.

    Do not assume rear damage, cook-off, or that the enemy has no armor. The
    lower damage fraction also stays safe if a charge curve has a minimum
    plateau rather than adding that minimum to a linear curve.
    """
    stats, charge = weapon['stats'], weapon.get('charge', 0)
    if charge < stats.get('min_charge', .15) or target.get('hp') is None:
        return False
    fraction = max(stats.get('min_frac', .35), min(1, max(0, charge-.005)/(stats['charge']+.0005)))
    cubes = config['cubes']
    factor = 1+cubes['damage_per_cube']*min(me.get('cubes', 0), cubes['stat_cap'])
    armor = limits['armor'] if target.get('kind') == 'mech' else 0
    damage = max(0, stats['damage']-.0005)*fraction*factor*(1-max(0, min(.95, armor)))
    # Allow an action interval plus a simulation tick of worst-case repair.
    healing = limits['regen_per_s']*.15 if target.get('kind') == 'mech' else 0
    hp_bound = min(target.get('max_hp', math.inf)+.01, target['hp']+healing+.01)
    return damage >= hp_bound


def time_to_kill(weapons, hp, distance=0, damage_factor=1, armor=0, horizon=30):
    """Optimistic gun/laser hit schedule, not a prediction of actual accuracy.

    Other weapon types are deliberately outside this first experiment. Callers
    must restrict the comparison to ranges where those weapons cannot matter.
    """
    if hp <= 0:
        return 0.0
    events = []
    scale = max(0, damage_factor)*(1-max(0, min(.95, armor)))
    for weapon in weapons:
        if not weapon:
            continue
        stats, kind = weapon['stats'], weapon['kind']
        if kind not in ('gun', 'laser') or distance > stats.get('range', 0):
            continue
        damage = stats['damage']*scale
        cooldown = max(.05, stats['cooldown'])
        if kind == 'gun':
            capacity = max(1, int(stats['mag']))
            reloading = max(0, weapon.get('reload', 0))
            ammo = capacity if reloading else max(0, int(weapon.get('ammo', capacity)))
            clock = max(reloading, weapon.get('cooldown', 0))
            if ammo == 0:
                clock = max(clock, stats['reload'])
                ammo = capacity
            travel = distance/max(1, stats['speed'])
            while clock+travel <= horizon:
                events.append((clock+travel, damage))
                ammo -= 1
                if ammo == 0:
                    clock += max(cooldown, stats['reload'])
                    ammo = capacity
                else:
                    clock += cooldown
        else:
            charge = max(.05, stats['charge'])
            clock = max(0, weapon.get('cooldown', 0))+max(0, charge-weapon.get('charge', 0))
            while clock <= horizon:
                events.append((clock, damage))
                clock += cooldown+charge
    accumulated = 0.0
    for clock, damage in sorted(events):
        accumulated += damage
        if accumulated+1e-7 >= hp:
            return clock
    return math.inf


def duel_times(me, enemy, config, distance):
    """Our kill time and incoming kill time, or no forecast outside scope."""
    kinds = enemy.get('weapons', ['gun'])
    if ('mortar' in kinds or any(w and w['kind'] == 'mortar' for w in me['weapons'])
            or distance <= config['weapons']['lightning']['range']+15
            or not any(w and w['kind'] in ('gun', 'laser') for w in me['weapons'])):
        return None
    other = []
    for kind in kinds:
        if kind in ('gun', 'laser'):
            other.append({'kind':kind, 'stats':config['weapons'][kind]})
    cubes = config['cubes']
    factor = lambda n: 1+cubes['damage_per_cube']*min(n, cubes['stat_cap'])
    outgoing = time_to_kill(me['weapons'], enemy.get('hp', enemy.get('max_hp', 1400)), distance,
                            damage_factor=factor(me['cubes']))
    incoming = time_to_kill(other, me['hp'], distance,
                            damage_factor=factor(enemy.get('cubes', 0)), armor=me['stats'].get('armor', 0))
    return outgoing, incoming
