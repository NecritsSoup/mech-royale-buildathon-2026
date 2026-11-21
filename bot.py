"""Soup Sentinel: survive, seek cover, rotate early, and fight only defensively."""
import math
import random
import time
import mechfight as mf
from geometry import World, Navigation, norm
from physics import angles, intercept, projectile_danger, wrap


class Bot:
    name = 'Soup Sentinel'

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
        mf.log(f'Loaded arena in {time.perf_counter()-started:.3f}s; {sum(self.nav.mask)} navigable cells')

    def size(self, actor):
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

    def upgrade(self, offer, me):
        best = (float('-inf'), 0)
        hp_fraction = me['hp'] / max(1, me['max_hp'])
        definitions = {u['id']: u for u in self.config.get('upgrades', [])}
        for i, choice in enumerate(offer['choices']):
            effects = definitions.get(choice['id'], {}).get('effects', [])
            score = 0.0
            for effect in effects:
                path = effect['path']
                delta = effect.get('mul', 1) - 1
                add = effect.get('add', 0)
                if path == 'mech.hp':
                    score += delta * (50 + (100 if me['hp']/me['max_hp'] < .4 else 35) * (1 - hp_fraction))
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
                    score += delta * 6
                elif path.endswith('.cooldown'):
                    score -= delta * 8
                elif path.endswith('.range'):
                    score += delta * 5
                elif path.endswith('.reload'):
                    score -= delta * 5
                elif path.endswith('.mag'):
                    score += add * .2
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
                and now-self.camp_time < (.7 if any(v and d < 140 for _, d, v in threats) else 3)):
            return self.camp
        candidates = []
        radial = max(0, min(90, radius * .55))
        ax, ay = norm(me['pos'][0]-center[0], me['pos'][1]-center[1])
        anchor_distance = min(math.dist(me['pos'][:2], center), max(0, radius*.68))
        anchor = [center[0]+ax*anchor_distance, center[1]+ay*anchor_distance]
        samples = [tuple(center), tuple(anchor)]
        for i in range(8):
            angle = i * math.tau / 8 + self.escape_angle
            samples.append((center[0]+radial*math.cos(angle), center[1]+radial*math.sin(angle)))
            for distance in (24, 48):
                samples.append((anchor[0]+distance*math.cos(angle), anchor[1]+distance*math.sin(angle)))
        for point in samples:
            node = self.nav.nearest(point, max_rings=4)
            if node is not None:
                point = self.nav.point(node)
                if math.dist(point, center) < max(12, radius * .82):
                    candidates.append(point)
        if not candidates:
            node = self.nav.nearest(center)
            return tuple(center) if node is None else self.nav.point(node)
        best, best_score = candidates[0], float('-inf')
        # Only the closest three threats meaningfully influence local cover choice.
        enemies = sorted(threats, key=lambda item: item[1])[:3]
        for point in candidates:
            score = -math.dist(point, me['pos'][:2]) * .08
            cover = self.world.cover_distance(*point)
            score += max(0, 18 - abs(cover - 8)) * .55
            z = self.world.height(*point) + me['stats'].get('height', 9) * .94
            for enemy, _, _ in enemies:
                dist = math.dist(point, enemy['pos'][:2])
                if dist < 230:
                    _, enemy_height = self.size(enemy)
                    muzzle_height = enemy_height * self.config['mech'].get('muzzle_height', 7) / self.config['mech'].get('height', 9) if enemy['kind'] == 'mech' else enemy_height*.5
                    enemy_muzzle = [*enemy['pos'][:2], enemy['pos'][2]+muzzle_height]
                    if self.world.line_clear(enemy_muzzle, [*point, z]):
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
        camp = self.choose_camp(obs, threats)
        speed = me['stats'].get('walk_speed', 11.7)
        remaining = zone['shrink_end'] - now
        future_dist = math.dist(me['pos'][:2], zone['next_center'])
        travel = max(0, future_dist - max(5, zone['next_radius'] * .75)) / max(1, speed)
        urgent = not me['in_zone'] or remaining < travel * 1.7 + 12
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
            if enemy_dist < dist + 30 or (visible_enemies and enemy_dist < 100):
                continue
            if drop.get('holder') not in (None, me['id']):
                continue
            if dist < 100 and drop['lands_in'] < dist / max(1, speed) + 5:
                priority = (110 if hp_fraction < .8 else 50) - dist * .6
                drops.append((priority, drop))
        if drops:
            _, drop = max(drops, key=lambda pair: pair[0])
            if math.dist(drop['pos'][:2], me['pos'][:2]) < max(1, drop['radius'] - me['stats'].get('radius', 2.5) - 1):
                return tuple(me['pos'][:2]), 'capture', False
            return tuple(drop['pos'][:2]), 'drop', False
        if visible_enemies and min(d for _, d in visible_enemies) < 140:
            return camp, 'hide', False
        if hp_fraction < .94:
            return camp, 'heal', False
        # Incidental loot is useful; do not cross the map or expose ourselves for it.
        loot = []
        if not visible_enemies:
            for cube in obs.get('cubes', []):
                dist = math.dist(cube['pos'][:2], me['pos'][:2])
                if me['cubes'] < 10 and dist < 35 and abs(cube['pos'][2]-me['pos'][2]) < 4 and self.safe_point(cube['pos'], zone, 20):
                    loot.append((45-dist, tuple(cube['pos'][:2]), 'cube'))
            held = {w['kind'] for w in me['weapons'] if w}
            for pickup in obs.get('pickups', []):
                dist = math.dist(pickup['pos'][:2], me['pos'][:2])
                if pickup['kind'] == 'lightning' and pickup['kind'] not in held and dist < 30 and abs(pickup['pos'][2]-me['pos'][2]) < 4 and self.safe_point(pickup['pos'], zone, 20):
                    value = 35
                    loot.append((value-dist, tuple(pickup['pos'][:2]), 'loot'))
        if loot:
            _, point, state = max(loot)
            return point, state, False
        return camp, 'camp', False

    def movement(self, obs, goal, state, urgent, threats):
        me, now = obs['self'], obs['t']
        radius, height = me['stats'].get('radius', 2.5), me['stats'].get('height', 9)
        desired = self.nav.direction(me['pos'], goal, now, radius + .3, height)
        danger_close = any(vis and d < 90 for _, d, vis in threats)
        damaged = now - self.last_damage < 1
        moving = math.dist(goal, me['pos'][:2]) > 4
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
        if not moving and not danger_close and not obs.get('projectiles'):
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
            if direction == (0.0, 0.0) and not moving:
                score += 8
            zone_distance = math.dist(end, obs['zone']['center'])
            if zone_distance > max(1, obs['zone']['radius'] - 5):
                score -= 120 + zone_distance - obs['zone']['radius']
            score -= projectile_danger(me, direction, speed, obs.get('projectiles', []), radius, height, gravity)
            for enemy, dist, visible in threats:
                if not visible or dist > 160:
                    continue
                separation = math.dist(end, enemy['pos'][:2])
                score += (separation - dist) * (1.8 if dist < 60 else .5)
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

    def shooting(self, obs, threats):
        me, now = obs['self'], obs['t']
        muzzle = [*me['pos'][:2], me['pos'][2] + me['stats'].get('muzzle_height', 7)]
        action = {'fire': [False, False], 'reload': False}
        # Covering the retreat takes priority; otherwise shooting gives away our hiding place.
        defensive = [(a, d) for a, d, visible in threats if visible and d < 70
                     and (now-self.last_damage < 2 or d < 40 or a.get('charging'))]
        if not defensive:
            for weapon in me['weapons']:
                if weapon and weapon['kind'] == 'gun' and weapon.get('reload', 0) <= 0 and weapon.get('ammo', 0) < weapon['stats'].get('mag', 12):
                    action['reload'] = True
            # Face the most recent attacker, or scan slowly when nobody is threatening.
            damage = [e for e in obs.get('events', []) if e['type'] == 'damaged' and e.get('bearing') is not None]
            if damage:
                action['aim'] = [damage[-1]['bearing'], 0.0]
            elif threats:
                enemy = min(threats, key=lambda item: item[1])[0]
                action['aim'] = angles(muzzle, self.target_center(enemy))
            else:
                sounds = [s for s in obs.get('sounds', []) if s['kind'] in ('gun', 'laser', 'laser_charge', 'mortar', 'lightning') and s['age'] < .5 and s['dist'] != 'far']
                action['aim'] = [sounds[-1]['bearing'], 0.0] if sounds else [wrap(self.escape_angle + now * .55), 0.0]
            return action
        enemy, distance = min(defensive, key=lambda item: item[1])
        center, velocity = self.target_center(enemy), enemy.get('vel', [0, 0, 0])
        aim_point = center
        gun = next((w for w in me['weapons'] if w and w['kind'] == 'gun'), None)
        if gun:
            prediction = intercept(muzzle, center, velocity, gun['stats'].get('speed', 252))
            if prediction:
                aim_point = prediction[0]
        desired = angles(muzzle, aim_point)
        desired[1] = max(me['stats'].get('min_pitch', -.7), min(me['stats'].get('max_pitch', 1.3), desired[1]))
        action['aim'] = desired
        target_radius, _ = self.size(enemy)
        tolerance = math.atan2(target_radius, max(1, distance)) + .012
        yaw_error, pitch_error = abs(wrap(desired[0]-me['aim'][0])), abs(desired[1]-me['aim'][1])
        clear = self.world.line_clear(muzzle, center)
        for i, weapon in enumerate(me['weapons']):
            if not weapon:
                continue
            stats, kind = weapon['stats'], weapon['kind']
            if kind == 'gun':
                if weapon.get('ammo', 0) <= 0 and weapon.get('reload', 0) <= 0:
                    action['reload'] = True
                action['fire'][i] = bool(clear and distance < stats.get('range', 230) and weapon.get('ammo', 0) > 0
                    and weapon.get('reload', 0) <= 0 and yaw_error < tolerance + me['stats'].get('turn_rate', 4)*.045
                    and pitch_error < tolerance + me['stats'].get('pitch_rate', 3)*.045)
            elif kind == 'lightning':
                action['fire'][i] = bool(clear and distance < stats.get('range', 45) - 1
                    and abs(wrap(angles(muzzle, center)[0]-me['aim'][0])) < math.radians(stats.get('cone_deg', 30))*.45
                    and me['energy'] > stats.get('energy', 3) + 35)
            elif kind == 'laser':
                # Charging halves movement, so only use it when an escape is not needed.
                charge = weapon.get('charge', 0)
                aligned = abs(wrap(angles(muzzle, center)[0]-me['aim'][0])) < tolerance
                if charge > 0:
                    action['aim'] = angles(muzzle, center)
                    action['fire'][i] = not (charge >= stats.get('charge', .75) and aligned)
                elif distance > 55 and me['hp']/me['max_hp'] > .85 and self.state == 'camp':
                    action['fire'][i] = bool(clear and aligned)
        return action

    def act(self, obs):
        me, now = obs['self'], obs['t']
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
            action['pick'] = self.upgrade(obs['offer'], me)
        held = {w['kind'] for w in me['weapons'] if w}
        for pickup in sorted(obs.get('pickups', []), key=lambda p: math.dist(p['pos'][:2], me['pos'][:2])):
            if (pickup['kind'] == 'lightning' and pickup['kind'] not in held
                    and math.dist(pickup['pos'][:2], me['pos'][:2]) <= self.config['loot']['pickup_radius']
                    and abs(pickup['pos'][2]-me['pos'][2]) <= 4):
                if any(w is None for w in me['weapons']):
                    action['take'] = pickup['id']
                elif pickup['kind'] == 'lightning':
                    slot = next((i for i, w in enumerate(me['weapons']) if w and w['kind'] in ('laser', 'mortar') and w.get('charge', 0) == 0), None)
                    if slot is not None:
                        action.update(take=pickup['id'], drop=slot)
                break
        return action


if __name__ == '__main__':
    mf.run(Bot)
