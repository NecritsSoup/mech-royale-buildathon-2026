"""Soup Sentinel: gear up, control engagement range, recover and rotate early."""
import math
import random
import time
import mechfight as mf
from geometry import World, Navigation, norm
from physics import angles, intercept, ballistic_intercept, projectile_danger, wrap
from combat_math import duel_times, laser_ready, laser_finishes, enemy_limits


class Bot:
    name = 'Soup Sentinel v5 test'

    def __init__(self, hello):
        self.config = hello['config']
        self.rng = random.Random(hello['seed'])
        started = time.perf_counter()
        self.world = World(hello['map']['dir'])
        self.nav = Navigation(self.world, self.config['mech'])
        self.camp, self.camp_time, self.camp_circle = None, -100, None
        self.last_damage = -100.0
        self.last_position, self.last_moving, self.stuck_since = None, False, None
        self.escape_angle = self.rng.random() * 2 * math.pi
        self.strafe_sign = -1 if hello['slot'] % 2 else 1
        self.last_strafe = 0
        self.state = 'init'
        self.jet_until = -100
        self.recharging = False
        self.recovering = False
        self.farm_target = None
        self.target_id = None
        self.attackers = {}
        self.last_attack_direction = None
        self.enemy_limits = enemy_limits(self.config)
        mf.log(f'Loaded arena in {time.perf_counter()-started:.3f}s; {sum(self.nav.mask)} navigable cells')

    def size(self, actor):
        if actor.get('kind') == 'crate':
            return self.config['crates']['radius'], self.config['crates']['height']
        if actor.get('kind') == 'npc':
            return self.config['npc']['radius'], self.config['npc']['height']
        factor = 1 + self.config['cubes'].get('size_per_cube', .05) * actor.get('cubes', 0)
        return self.config['mech']['radius'] * factor, self.config['mech']['height'] * factor

    def target_center(self, actor):
        _, height = self.size(actor)
        return [*actor['pos'][:2], actor['pos'][2] + height * .5]

    def visibility(self, me, actors):
        eye = [*me['pos'][:2], me['pos'][2] + me['stats'].get('eye_height', 8)]
        output = []
        for actor in actors:
            distance = math.dist(actor['pos'][:2], me['pos'][:2])
            # Observations include positions remembered behind walls for five seconds.
            center = self.target_center(actor)
            _, actor_height = self.size(actor)
            top = [*actor['pos'][:2], actor['pos'][2] + actor_height - .25]
            visible = self.world.line_clear(eye, center) or self.world.line_clear(eye, top)
            output.append((actor, distance, visible))
        return output

    def upgrade(self, offer, me, now=0):
        best = (float('-inf'), 0)
        hp_fraction = me['hp'] / max(1, me['max_hp'])
        definitions = {u['id']: u for u in self.config.get('upgrades', [])}
        for i, choice in enumerate(offer['choices']):
            effects = definitions.get(choice['id'], {}).get('effects', [])
            score = 0.0
            for effect in effects:
                path = effect['path']
                previous_score = score
                delta = effect.get('mul', 1) - 1
                add = effect.get('add', 0)
                if path == 'mech.hp':
                    score += delta * ((200 if now > 300 else 60) + (120 if hp_fraction < .4 else 40) * (1 - hp_fraction))
                elif path == 'mech.armor':
                    score += add * 80
                elif path == 'mech.regen_per_s':
                    score += delta * 17
                elif path == 'mech.regen_delay':
                    score -= add * 4
                elif path in ('mech.walk_speed', 'mech.boost_speed'):
                    score += delta * 40
                elif path in ('mech.boost_drain', 'mech.jet_drain'):
                    score -= delta * 12
                elif path == 'mech.energy_regen':
                    score += delta * 12
                elif path == 'mech.energy':
                    score += add * .12
                elif path == 'mech.near_range_bonus':
                    score += add * .15
                elif path in ('mech.turn_rate', 'mech.pitch_rate'):
                    score += delta * 5
                elif path.endswith('.damage'):
                    score += delta * 45
                elif path.endswith('.cooldown'):
                    score -= delta * 45
                elif path.endswith('.range'):
                    score += delta * 5
                elif path.endswith('.reload'):
                    score -= delta * 12
                elif path.endswith('.mag'):
                    score += add * .9
                elif path.endswith('.charge'):
                    weapon = next((w for w in me['weapons'] if w and w['kind'] == 'laser'), None)
                    stats = weapon['stats'] if weapon else self.config['weapons']['laser']
                    before = stats['charge']+stats['cooldown']
                    after = max(.05, stats['charge']*effect.get('mul', 1)+add)+stats['cooldown']
                    score += 45*(before/after-1)
                elif path.endswith('.speed'):
                    score += delta * 12
                elif path.endswith('.energy'):
                    score -= delta * 20
                if path.startswith('weapons.'):
                    kind = path.split('.')[1]
                    weapon = next((w for w in me['weapons'] if w and w['kind'] == kind), None)
                    relevance = .1 if weapon is None else 1
                    if weapon and kind == 'lightning' and self.preferred_range(me) > weapon['stats']['range']:
                        relevance = .35  # Still useful as a defensive secondary.
                    score = previous_score+(score-previous_score)*relevance
            if score > best[0]:
                best = score, i
        return best[1]

    def safe_point(self, point, circle, margin=5):
        return math.dist(point[:2], circle['center']) < max(0, circle['radius'] - margin)

    def choose_camp(self, obs, threats):
        me, zone, now = obs['self'], obs['zone'], obs['t']
        center, radius = zone['next_center'], zone['next_radius']
        # Re-evaluate after damage or enemy approach, otherwise preserve a stable route.
        if (self.camp is not None and self.camp_circle is not None
                and math.dist(center, self.camp_circle) < 8
                and math.dist(self.camp, center) < max(12, radius*.82)
                and self.safe_point(self.camp, zone, margin=8)
                and now-self.camp_time < (.7 if any(v and d < 140 for _, d, v in threats) else 3)):
            return self.camp
        candidates = []
        radial = max(0, min(90, radius * .55))
        ax, ay = norm(me['pos'][0]-center[0], me['pos'][1]-center[1])
        anchor_distance = min(math.dist(me['pos'][:2], center), max(0, radius*.68))
        anchor = [center[0]+ax*anchor_distance, center[1]+ay*anchor_distance]
        samples = [tuple(center), tuple(anchor)]
        body_radius, body_height = self.size(me)
        samples.extend(self.world.cover_points(anchor, body_radius+.35, body_height))
        for i in range(8):
            angle = i * math.tau / 8 + self.escape_angle
            samples.append((center[0]+radial*math.cos(angle), center[1]+radial*math.sin(angle)))
            for distance in (24, 48):
                samples.append((anchor[0]+distance*math.cos(angle), anchor[1]+distance*math.sin(angle)))
        seen = set()
        for point in samples:
            node = self.nav.nearest(point, max_rings=4)
            if node is not None and node not in seen:
                seen.add(node)
                point = self.nav.point(node)
                if math.dist(point, center) < max(12, radius * .82):
                    candidates.append(point)
        if not candidates:
            node = self.nav.nearest(center)
            return tuple(center) if node is None else self.nav.point(node)
        near_ground = me['pos'][2] <= self.world.height(*me['pos'][:2])+max(2, me['stats'].get('step_up', 1.2)+.5)
        reachable = [point for point in candidates if self.nav.connected(me['pos'], point)] if near_ground else candidates
        # If every future point needs a jump/jet, retain the previous escape
        # fallback rather than deciding to stand still inside a sealed area.
        if reachable:
            candidates = reachable
        best, best_score = candidates[0], float('-inf')
        # Only the closest three threats meaningfully influence local cover choice.
        enemies = sorted(threats, key=lambda item: item[1])[:3]
        for point in candidates:
            score = -math.dist(point, me['pos'][:2]) * .08
            cover = self.world.cover_distance(*point)
            score += max(0, 18 - abs(cover - 8)) * .55
            ground = self.world.height(*point)
            for enemy, _, _ in enemies:
                dist = math.dist(point, enemy['pos'][:2])
                danger_range = max([230, *[self.config['weapons'][kind].get('range', 0)
                    for kind in enemy.get('weapons', []) if kind in self.config['weapons']]])
                if dist < danger_range:
                    _, enemy_height = self.size(enemy)
                    muzzle_height = enemy_height * self.config['mech'].get('muzzle_height', 7) / self.config['mech'].get('height', 9) if enemy['kind'] == 'mech' else enemy_height*.5
                    enemy_muzzle = [*enemy['pos'][:2], enemy['pos'][2]+muzzle_height]
                    exposed = any(self.world.line_clear(enemy_muzzle, [*point, ground+body_height*f])
                                  for f in (.94, .5, .15))
                    if exposed:
                        score -= 65 / (1 + dist / 60)
                    else:
                        score += 14
            if not self.safe_point(point, zone, margin=8):
                score -= 200
            if score > best_score:
                best, best_score = point, score
        self.camp, self.camp_time, self.camp_circle = tuple(best), now, tuple(center)
        return self.camp

    def objective(self, obs, threats):
        me, zone, now = obs['self'], obs['zone'], obs['t']
        self.farm_target = None
        health = me['hp'] / max(1, me['max_hp'])
        self.recovering = health < .5 or self.recovering and health < .86
        camp = self.choose_camp(obs, threats)
        speed = me['stats'].get('walk_speed', 11.7)
        remaining = zone['shrink_end'] - now
        future_dist = math.dist(me['pos'][:2], zone['next_center'])
        travel = max(0, future_dist - max(5, zone['next_radius'] * .75)) / max(1, speed)
        urgent = not me['in_zone'] or remaining < travel * 1.7 + 12
        if not urgent and future_dist > max(5, zone['next_radius'] * .75):
            # A nearby safe circle may be a long walk around a building. A
            # separate cached probe preserves the route used by movement.
            route = self.nav.route_distance(me['pos'], camp, now)
            travel = max(travel, route / max(1, speed))
            urgent = remaining < travel * 1.7 + 12
        hp_fraction = me['hp'] / max(1, me['max_hp'])
        visible_enemies = [(a, d) for a, d, vis in threats if vis and d < 180]
        if urgent:
            return camp, 'zone', True
        # Full-heal supply drops are worth detours only when uncontested.
        drops = []
        for drop in obs.get('drops', []):
            dist = math.dist(drop['pos'][:2], me['pos'][:2])
            if abs(drop['pos'][2] - self.world.height(*drop['pos'][:2])) > 3:
                continue
            if not self.safe_point(drop['pos'], zone, margin=20):
                continue
            enemy_dist = min((math.dist(a['pos'][:2], drop['pos'][:2]) for a, _, _ in threats), default=1000)
            remaining_capture = max(0, drop['capture_s']-drop.get('progress', 0))
            contest_time = min((max(0, math.dist(a['pos'][:2], drop['pos'][:2])-drop['radius'])/self.enemy_limits['boost_speed']
                                for a,_,_ in threats if a['kind'] == 'mech'), default=math.inf)
            finish_banked = (drop.get('holder') == me['id'] and drop['lands_in'] <= 0
                and dist < drop['radius']-1 and remaining_capture <= 1 and hp_fraction > .65
                and contest_time > remaining_capture+.75)
            if not finish_banked and (enemy_dist < dist + 30 or (visible_enemies and enemy_dist < 100)):
                continue
            if drop.get('holder') not in (None, me['id']):
                continue
            if dist < 100 and drop['lands_in'] < dist / max(1, speed) + 5:
                priority = (110 if hp_fraction < .8 else 50) - dist * .6
                drops.append((priority, drop))
        if drops:
            _, drop = max(drops, key=lambda pair: pair[0])
            if math.dist(drop['pos'][:2], me['pos'][:2]) < max(1, drop['radius']-1):
                return tuple(me['pos'][:2]), 'capture', False
            return tuple(drop['pos'][:2]), 'drop', False
        if self.recovering:
            return camp, 'heal', False
        target = self.select_target(obs, threats)
        close_mechs = sum(a['kind'] == 'mech' and d < 150 for a, d in visible_enemies)
        if close_mechs > 2:
            return camp, 'hide', False
        # Empty magazines are a reason to duck behind cover, rather than stand
        # exchanging damage with a weapon that cannot fire.
        guns = [w for w in me['weapons'] if w and w['kind'] == 'gun']
        alternate_ready = False
        if target:
            distance = target[1]
            for weapon in me['weapons']:
                if not weapon or weapon['kind'] == 'gun' or weapon.get('cooldown', 0) >= .3:
                    continue
                kind, stats = weapon['kind'], weapon['stats']
                if kind == 'lightning':
                    usable = distance < stats.get('range', 45)-1 and me['energy'] > stats.get('energy', 3)+12
                elif kind == 'mortar':
                    usable = 22 < distance < stats.get('speed', 72)**2/max(1, stats.get('gravity', 22))
                else:
                    usable = distance < stats.get('range', 280)
                alternate_ready = alternate_ready or usable
        if guns and guns[0].get('ammo', 0) == 0 and not alternate_ready and visible_enemies:
            return camp, 'reload', False
        if (any(w is None for w in me['weapons']) and health > .7
                and min((d for _, d in visible_enemies), default=1000) > 80):
            equipment = []
            for pickup in obs.get('pickups', []):
                distance = math.dist(me['pos'][:2], pickup['pos'][:2])
                nearest_enemy = min((math.dist(a['pos'][:2], pickup['pos'][:2]) for a, _, _ in threats if a['kind'] == 'mech'), default=1000)
                if (self.pickup_slot(me, pickup['kind']) is not None and distance < 50
                        and abs(pickup['pos'][2]-me['pos'][2]) < 4 and nearest_enemy > 60
                        and self.safe_point(pickup['pos'], zone, 20)):
                    equipment.append((distance, pickup))
            if equipment:
                pickup = min(equipment, key=lambda pair: pair[0])[1]
                return tuple(pickup['pos'][:2]), 'loot', False
        if target:
            enemy, distance = target
            first_phase = self.config['zone']['phases'][0]
            opening_end = first_phase['hold_s']+first_phase['shrink_s']
            if enemy['kind'] == 'mech' and now < opening_end:
                forecast = duel_times(me, enemy, self.config, distance)
                if forecast:
                    kill_time, death_time = forecast
                    # This is a new hypothesis, with no claim about the ladder:
                    # avoid a clearly losing opening duel, but allow a fast finish.
                    if kill_time > 1.2 and kill_time > death_time*1.35:
                        return camp, 'retreat', False
            if health > .7 and distance > 60 and enemy.get('hp', 1400) >= 220:
                growth = self.nearby_cube(obs, threats)
                if growth is not None:
                    return growth, 'cube', False
            preferred = self.preferred_range(me, enemy)
            if distance > preferred + 8:
                dx, dy = norm(me['pos'][0]-enemy['pos'][0], me['pos'][1]-enemy['pos'][1])
                point = (enemy['pos'][0]+dx*preferred, enemy['pos'][1]+dy*preferred)
                if self.safe_point(point, zone, margin=10):
                    return point, 'fight', False
            return tuple(me['pos'][:2]), 'fight', False
        if hp_fraction < .85:
            return camp, 'heal', False
        # Incidental loot is useful; do not cross the map or expose ourselves for it.
        loot = []
        if not visible_enemies:
            for cube in obs.get('cubes', []):
                dist = math.dist(cube['pos'][:2], me['pos'][:2])
                if me['cubes'] < 10 and dist < 55 and abs(cube['pos'][2]-me['pos'][2]) < 4 and self.safe_point(cube['pos'], zone, 20):
                    loot.append((80-dist, tuple(cube['pos'][:2]), 'cube'))
            held = {w['kind'] for w in me['weapons'] if w}
            for pickup in obs.get('pickups', []):
                dist = math.dist(pickup['pos'][:2], me['pos'][:2])
                if self.pickup_slot(me, pickup['kind']) is not None and dist < 65 and abs(pickup['pos'][2]-me['pos'][2]) < 4 and self.safe_point(pickup['pos'], zone, 20):
                    value = 90 if pickup['kind'] in ('laser', 'lightning') else 55
                    loot.append((value-dist, tuple(pickup['pos'][:2]), 'loot'))
        if loot:
            _, point, state = max(loot)
            return point, state, False
        # Early, uncontested crates turn the waiting time into weapons and HP.
        # Avoid firing during a recovery interval or crossing the zone for them.
        if not visible_enemies and health > .85 and now < 140 and me['cubes'] < 8:
            crates = []
            muzzle = [*me['pos'][:2], me['pos'][2]+me['stats'].get('muzzle_height', 7)]
            for crate in obs.get('crates', []):
                distance = math.dist(crate['pos'][:2], me['pos'][:2])
                point = [*crate['pos'][:2], crate['pos'][2]+self.config['crates']['height']*.5]
                if (distance < 65 and abs(crate['pos'][2]-me['pos'][2]) < 4
                        and self.safe_point(crate['pos'], zone, 20)
                        and self.world.line_clear(muzzle, point)):
                    crates.append((distance+crate['hp']*.02, crate))
            if crates:
                crate = min(crates, key=lambda pair: pair[0])[1]
                self.farm_target = dict(crate, kind='crate')
                distance = math.dist(crate['pos'][:2], me['pos'][:2])
                if distance < 35:
                    return tuple(me['pos'][:2]), 'farm', False
                dx, dy = norm(me['pos'][0]-crate['pos'][0], me['pos'][1]-crate['pos'][1])
                return (crate['pos'][0]+dx*30, crate['pos'][1]+dy*30), 'farm', False
        return camp, 'camp', False

    def nearby_cube(self, obs, threats):
        """A short, walkable pickup without entering a closing enemy's reach."""
        me = obs['self']
        if me['cubes'] >= self.config['cubes']['stat_cap']:
            return None
        radius, height = self.size(me)
        candidates = []
        for cube in obs.get('cubes', []):
            distance = math.dist(me['pos'][:2], cube['pos'][:2])
            if distance > 12 or abs(cube['pos'][2]-me['pos'][2]) > 4 or not self.safe_point(cube['pos'], obs['zone'], 20):
                continue
            speed = me['stats'].get('walk_speed', 11.7)
            if any(w and w['kind'] == 'laser' for w in me['weapons']):
                speed *= me['stats'].get('charge_move_mult', .5)
            arrival = max(0, distance-self.config['cubes']['pickup_radius'])/max(1, speed)+.05
            enemy_gap = min((math.dist(actor['pos'][:2], cube['pos'][:2])-self.enemy_limits['boost_speed']*arrival
                             for actor,_,visible in threats if visible and actor['kind'] == 'mech'), default=math.inf)
            if enemy_gap <= self.config['weapons']['lightning']['range']:
                continue
            if self.world.walk_clear(me['pos'], cube['pos'][:2], radius+.35, height,
                    me['stats'].get('step_up', 1.2), me['stats'].get('max_slope_deg', 50)):
                candidates.append((distance, tuple(cube['pos'][:2])))
        return min(candidates)[1] if candidates else None

    def movement(self, obs, goal, state, urgent, threats):
        me, now = obs['self'], obs['t']
        radius, height = self.size(me)
        desired = self.nav.direction(me['pos'], goal, now, radius + .3, height)
        danger_close = any(vis and d < 90 for _, d, vis in threats)
        damaged = now - self.last_damage < 1
        moving = math.dist(goal, me['pos'][:2]) > (1.0 if state in ('cube', 'loot') else 4)
        fighting = state == 'fight'
        energy_fraction = me['energy'] / max(1, me['max_energy'])
        gravity = self.config['weapons']['mortar'].get('gravity', 22)
        incoming = projectile_danger(me, (0, 0), 0, obs.get('projectiles', []), radius, height, gravity) > 10
        if energy_fraction < .25:
            self.recharging = True
        elif energy_fraction > .7:
            self.recharging = False
        boost = not self.recharging and me['grounded'] and (moving and urgent and energy_fraction > .18
                    or (danger_close or incoming or damaged) and energy_fraction > .4)
        if not me['grounded'] and now < self.jet_until and me['energy'] > 20:
            boost = True
        speed = me['stats'].get('boost_speed' if boost else 'walk_speed', 19.5 if boost else 11.7)
        if any(w and w['kind'] == 'laser' and w.get('charge', 0) > 0 for w in me['weapons']):
            speed *= me['stats'].get('charge_move_mult', .5)
        if not moving and not danger_close and not obs.get('projectiles') and not fighting:
            return [0.0, 0.0], False, False
        if desired == (0.0, 0.0):
            desired = norm(goal[0] - me['pos'][0], goal[1] - me['pos'][1])
        yaw = math.atan2(desired[1], desired[0])
        if now-self.last_strafe > 1.3:
            self.strafe_sign *= -1
            self.last_strafe = now
        best, best_score = (0.0, 0.0), float('-inf')
        choices = [(0.0, 0.0)]
        choices += [(math.cos(yaw + offset), math.sin(yaw + offset))
                    for offset in (0, .4, -.4, .8, -.8, math.pi/2, -math.pi/2, math.pi)]
        for direction in choices:
            if direction != (0.0, 0.0):
                end = [me['pos'][0] + direction[0] * speed * .65, me['pos'][1] + direction[1] * speed * .65]
                if not self.world.walk_clear(me['pos'], end, radius + .35, height,
                                             me['stats'].get('step_up', 1.2), me['stats'].get('max_slope_deg', 50)):
                    continue
            else:
                end = me['pos'][:2]
            score = (direction[0] * desired[0] + direction[1] * desired[1]) * (14 if moving else 0)
            if direction == (0.0, 0.0) and not moving and not fighting:
                score += 8
            zone_distance = math.dist(end, obs['zone']['center'])
            if zone_distance > max(1, obs['zone']['radius'] - 5):
                score -= 120 + zone_distance - obs['zone']['radius']
            score -= projectile_danger(me, direction, speed, obs.get('projectiles', []), radius, height, gravity)
            for enemy, dist, visible in threats:
                if not visible or dist > 160:
                    continue
                separation = math.dist(end, enemy['pos'][:2])
                if fighting:
                    preferred = self.preferred_range(me, enemy)
                    score -= abs(separation-preferred)*.9
                    ex, ey = norm(me['pos'][0]-enemy['pos'][0], me['pos'][1]-enemy['pos'][1])
                    score += self.strafe_sign*8*(direction[0]*-ey+direction[1]*ex)
                else:
                    score += (separation - dist) * (.2 if urgent else (1.8 if dist < 60 else .5))
                if state in ('heal', 'hide', 'reload', 'retreat'):
                    enemy_muzzle = [*enemy['pos'][:2], enemy['pos'][2]+self.size(enemy)[1]*.8]
                    own_head = [*end, self.world.height(*end)+height*.94]
                    if self.world.line_clear(enemy_muzzle, own_head):
                        score -= 14/(1+dist/90)
                # Dodge sideways when an enemy laser is pointing at us.
                if enemy.get('charging'):
                    bearing = math.atan2(me['pos'][1]-enemy['pos'][1], me['pos'][0]-enemy['pos'][0])
                    if abs(wrap(bearing-enemy['aim'][0])) < .13:
                        dx, dy = end[0]-enemy['pos'][0], end[1]-enemy['pos'][1]
                        beam_gap = abs(dx*math.sin(enemy['aim'][0])-dy*math.cos(enemy['aim'][0]))
                        score -= 60 * max(0, 1-beam_gap/(radius+4))
            if danger_close or incoming:
                score += self.strafe_sign * .3 * (direction[0]*-math.sin(yaw)+direction[1]*math.cos(yaw))
            if score > best_score:
                best, best_score = direction, score
        moved = math.dist(me['pos'][:2], self.last_position[:2]) if self.last_position else 100
        if self.last_moving and moved < .08 and me['grounded']:
            if self.stuck_since is None:
                self.stuck_since = now
        else:
            self.stuck_since = None
        jump = self.stuck_since is not None and now-self.stuck_since > .9 and me['energy'] > 45
        if jump:
            self.nav.planned_at = -100
            self.escape_angle += math.pi/2
            self.camp_time = -100
            self.jet_until = now + 2
            boost = True
        self.last_position, self.last_moving = tuple(me['pos']), moving
        return list(best), bool(boost and (math.hypot(*best) > .1 or not me['grounded'] or jump)), bool(jump)

    def select_target(self, obs, threats):
        me, now = obs['self'], obs['t']
        health = me['hp'] / max(1, me['max_hp'])
        nearby = sum(visible and d < 155 and a['kind'] == 'mech' for a, d, visible in threats)
        attackers = set(self.attackers) | {e.get('by') for e in obs.get('events', []) if e['type'] == 'damaged'}
        laser = any(w and w['kind'] == 'laser' for w in me['weapons'])
        candidates = []
        for actor, distance, visible in threats:
            if not visible:
                continue
            hp = actor.get('hp', 1400)
            if actor['kind'] == 'npc':
                engage = distance < 65 and (distance < 45 or actor['id'] in attackers
                         or health > .85 and nearby == 0 and now < 100 and me['cubes'] < 6)
                if nearby and actor['id'] not in attackers and distance > 20:
                    engage = False
            else:
                limit = 190 if laser else 160
                bearing = math.atan2(me['pos'][1]-actor['pos'][1], me['pos'][0]-actor['pos'][0])
                pointing = abs(wrap(bearing-actor.get('aim', [0, 0])[0])) < .13
                defensive = distance < 50 or actor['id'] in attackers or actor.get('charging') and pointing
                finish = hp < 220 and distance < limit
                opportunity = health > .6 and nearby <= 2 and pointing and distance < (175 if laser else 135)
                engage = distance < limit and (defensive or finish or opportunity)
                if (self.recovering or health < .5) and distance > 45 and not finish:
                    engage = False
            if engage:
                score = distance*.16 + hp/max(1, actor.get('max_hp', 1400))*24
                score -= 20 if actor['id'] in attackers else 0
                score -= 10 if actor['id'] == self.target_id else 0
                score -= 12 if actor.get('charging') else 0
                candidates.append((score, actor, distance))
        if not candidates:
            return None
        _, actor, distance = min(candidates, key=lambda item: item[0])
        return actor, distance

    def preferred_range(self, me, enemy=None):
        held = {w['kind']: w for w in me['weapons'] if w}
        if enemy and enemy.get('kind') == 'npc':
            return 32 if 'lightning' in held else 48
        if 'laser' in held:
            return 130
        if 'gun' in held:
            return 65 if 'lightning' in held else 75
        if 'mortar' in held:
            return 95
        return held.get('lightning', {}).get('stats', {}).get('range', 45)*.85

    def pickup_slot(self, me, kind):
        held = [w['kind'] if w else None for w in me['weapons']]
        if kind in held or kind == 'gun':
            return None
        if None in held:
            return held.index(None)
        pairs = {frozenset(('gun', 'laser')): 75, frozenset(('gun', 'lightning')): 65,
                 frozenset(('gun', 'mortar')): 45, frozenset(('laser', 'lightning')): 95,
                 frozenset(('laser', 'mortar')): 55, frozenset(('lightning', 'mortar')): 50}
        current = pairs.get(frozenset(held), 0)
        best, slot = current, None
        for i, weapon in enumerate(me['weapons']):
            if weapon and weapon['kind'] == 'laser' and weapon.get('charge', 0) > 0:
                continue
            value = pairs.get(frozenset((kind, held[1-i])), 0)
            if value > best:
                best, slot = value, i
        return slot

    def shooting(self, obs, threats):
        me, now = obs['self'], obs['t']
        muzzle = [*me['pos'][:2], me['pos'][2] + me['stats'].get('muzzle_height', 7)]
        action = {'fire': [False, False], 'reload': False}
        target = self.select_target(obs, threats)
        if self.state == 'retreat' and target and target[1] > 45:
            target = None
        if (self.state in ('heal', 'hide', 'reload') and target and target[1] > 45
                and target[0].get('hp', 1400) >= 220):
            target = None
        if target is None and self.farm_target is not None and self.state == 'farm':
            target = self.farm_target, math.dist(me['pos'][:2], self.farm_target['pos'][:2])
        if target is None:
            for weapon in me['weapons']:
                if weapon and weapon['kind'] == 'gun' and weapon.get('reload', 0) <= 0 and weapon.get('ammo', 0) < weapon['stats'].get('mag', 12):
                    action['reload'] = True
            # Face the most recent attacker, or scan slowly when nobody is threatening.
            damage = [e for e in obs.get('events', []) if e['type'] == 'damaged' and e.get('bearing') is not None]
            if damage:
                action['aim'] = [damage[-1]['bearing'], 0.0]
            elif self.last_attack_direction is not None and now-self.last_attack_direction[0] < 2:
                action['aim'] = [self.last_attack_direction[1], 0.0]
            elif threats:
                enemy = min(threats, key=lambda item: item[1])[0]
                action['aim'] = angles(muzzle, self.target_center(enemy))
            else:
                sounds = [s for s in obs.get('sounds', []) if s['kind'] in ('gun', 'laser', 'laser_charge', 'mortar', 'lightning') and s['age'] < .5 and s['dist'] != 'far']
                action['aim'] = [sounds[-1]['bearing'], 0.0] if sounds else [wrap(self.escape_angle + now * .55), 0.0]
            return action
        enemy, distance = target
        self.target_id = enemy['id']
        center, velocity = self.target_center(enemy), enemy.get('vel', [0, 0, 0])
        if not self.world.line_clear(muzzle, center):
            # A torso hidden by a low wall does not make the exposed head immune.
            top = [*enemy['pos'][:2], enemy['pos'][2]+self.size(enemy)[1]*.9]
            if self.world.line_clear(muzzle, top):
                center = top
        aim_point = center
        laser = next((w for w in me['weapons'] if w and w['kind'] == 'laser'), None)
        gun = next((w for w in me['weapons'] if w and w['kind'] == 'gun'), None)
        release_laser = laser is not None and (laser_ready(laser) or laser_finishes(laser, me, enemy, self.config, self.enemy_limits))
        # Both slots share one torso. Give an imminent hitscan beam its own aim.
        laser_aim = laser is not None and (gun is None
                    or release_laser or laser.get('charge', 0) >= laser['stats'].get('charge', .75)-.05)
        if gun:
            prediction = intercept(muzzle, center, velocity, gun['stats'].get('speed', 252))
            if prediction:
                aim_point = prediction[0]
        desired = angles(muzzle, aim_point)
        gun_aim = angles(muzzle, aim_point)
        if laser_aim:
            desired = angles(muzzle, center)
        mortar = next((w for w in me['weapons'] if w and w['kind'] == 'mortar'), None)
        mortar_solution = None
        if mortar and mortar.get('cooldown', 0) <= 0 and distance > 22:
            tube = [*me['pos'][:2], me['pos'][2]+self.size(me)[1]+.5]
            stats = mortar['stats']
            mortar_solution = ballistic_intercept(tube, center, velocity, stats.get('speed', 72), stats.get('gravity', 22))
            if mortar_solution:
                mortar_aim, flight = mortar_solution
                if not me['stats'].get('min_pitch', -.7) < mortar_aim[1] < me['stats'].get('max_pitch', 1.3):
                    mortar_solution = None
                else:
                    count = max(4, math.ceil(flight/.15))
                    previous = tube
                    for k in range(1, count+1):
                        t = flight*k/count
                        xy = stats.get('speed', 72)*math.cos(mortar_aim[1])*t
                        point = [tube[0]+xy*math.cos(mortar_aim[0]), tube[1]+xy*math.sin(mortar_aim[0]),
                                 tube[2]+stats.get('speed', 72)*math.sin(mortar_aim[1])*t-.5*stats.get('gravity', 22)*t*t]
                        if not self.world.line_clear(previous, point):
                            mortar_solution = None
                            break
                        previous = point
            if mortar_solution and not laser_aim:
                desired = mortar_solution[0]
        desired[1] = max(me['stats'].get('min_pitch', -.7), min(me['stats'].get('max_pitch', 1.3), desired[1]))
        action['aim'] = desired
        target_radius, _ = self.size(enemy)
        tolerance = math.atan2(target_radius*.85, max(1, distance)) + .006
        clear = self.world.line_clear(muzzle, center)
        for i, weapon in enumerate(me['weapons']):
            if not weapon:
                continue
            stats, kind = weapon['stats'], weapon['kind']
            if kind == 'gun':
                if weapon.get('ammo', 0) <= 0 and weapon.get('reload', 0) <= 0:
                    action['reload'] = True
                # Aim slews before a shot, but both slots share the command.
                # Predict that slew instead of giving every shot a wide slack.
                dt = .05 if weapon.get('cooldown', 0) <= .05 else .1
                yaw_step = me['stats'].get('turn_rate', 4)*dt
                pitch_step = me['stats'].get('pitch_rate', 3)*dt
                next_yaw = me['aim'][0]+max(-yaw_step, min(yaw_step, wrap(desired[0]-me['aim'][0])))
                next_pitch = me['aim'][1]+max(-pitch_step, min(pitch_step, desired[1]-me['aim'][1]))
                action['fire'][i] = bool(clear and distance < stats.get('range', 230) and weapon.get('ammo', 0) > 0
                    and weapon.get('reload', 0) <= 0 and abs(wrap(gun_aim[0]-next_yaw)) < tolerance
                    and abs(gun_aim[1]-next_pitch) < tolerance)
            elif kind == 'lightning':
                reserve = 12 if distance < 35 else 25
                if self.state == 'zone':
                    reserve = max(reserve, me['stats'].get('boost_drain', 17)*2)
                action['fire'][i] = bool(clear and distance < stats.get('range', 45) - 1
                    and abs(wrap(angles(muzzle, center)[0]-me['aim'][0])) < math.radians(stats.get('cone_deg', 30))*.9
                    and me['energy'] > stats.get('energy', 3) + reserve)
            elif kind == 'laser':
                if self.state == 'zone':
                    continue  # Release the charge rather than halve escape speed.
                charge = weapon.get('charge', 0)
                aligned = abs(wrap(angles(muzzle, center)[0]-me['aim'][0])) < tolerance
                aligned = aligned and abs(angles(muzzle, center)[1]-me['aim'][1]) < tolerance
                if charge > 0:
                    if laser_aim:
                        action['aim'] = angles(muzzle, center)
                    # Release only when aligned; do not hold past the automatic-fire limit.
                    action['fire'][i] = bool(clear and not (release_laser and aligned))
                elif weapon.get('cooldown', 0) <= 0 and distance < stats.get('range', 280):
                    action['fire'][i] = bool(clear and not (self.recovering and distance > 45))
            elif kind == 'mortar' and mortar_solution is not None and not laser_aim:
                mortar_aim = mortar_solution[0]
                error = math.atan2(stats.get('splash_radius', 7)*.5, max(1, distance))
                action['fire'][i] = bool(abs(wrap(mortar_aim[0]-me['aim'][0])) < error+.05
                                        and abs(mortar_aim[1]-me['aim'][1]) < error+.04)
        return action

    def act(self, obs):
        me, now = obs['self'], obs['t']
        self.attackers = {actor: at for actor, at in self.attackers.items() if now-at < 8}
        for event in obs.get('events', []):
            if event['type'] == 'damaged' and event.get('by') is not None:
                self.attackers[event['by']] = now
                if event.get('bearing') is not None:
                    self.last_attack_direction = (now, event['bearing'])
        if any(e['type'] == 'damaged' for e in obs.get('events', [])):
            self.last_damage = now
        threats = self.visibility(me, [a for a in obs.get('mechs', []) if a['kind'] == 'mech' or math.dist(a['pos'][:2], me['pos'][:2]) < self.config['npc'].get('deaggro_range', 90)])
        goal, self.state, urgent = self.objective(obs, threats)
        move, boost, jump = self.movement(obs, goal, self.state, urgent, threats)
        action = {'tick': obs['tick'], 'move': move, 'boost': boost, 'jump': jump}
        action.update(self.shooting(obs, threats))
        action['aim'][0] = wrap(action['aim'][0])
        action['aim'][1] = max(me['stats'].get('min_pitch', -.7), min(me['stats'].get('max_pitch', 1.3), action['aim'][1]))
        if obs.get('offer'):
            action['pick'] = self.upgrade(obs['offer'], me, now)
        held = {w['kind'] for w in me['weapons'] if w}
        for pickup in sorted(obs.get('pickups', []), key=lambda p: math.dist(p['pos'][:2], me['pos'][:2])):
            if (self.pickup_slot(me, pickup['kind']) is not None
                    and math.dist(pickup['pos'][:2], me['pos'][:2]) <= self.config['loot']['pickup_radius']
                    and abs(pickup['pos'][2]-me['pos'][2]) <= 4):
                slot = self.pickup_slot(me, pickup['kind'])
                if me['weapons'][slot] is None:
                    action['take'] = pickup['id']
                else:
                    action.update(take=pickup['id'], drop=slot)
                break
        return action


if __name__ == '__main__':
    mf.run(Bot)
