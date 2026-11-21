import copy
import gzip
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from array import array

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from geometry import World, Prism, Navigation
from physics import intercept, projectile_danger
from bot import Bot


def rectangle(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def make_world(directory, prisms=None):
    root = Path(directory) / 'v2'
    root.mkdir()
    data = {'bounds': {'minX': 0, 'maxX': 100, 'minY': 0, 'maxY': 100},
            'terrain': {'minX': 0, 'minY': 0, 'step': 1, 'nx': 101, 'ny': 101, 'scale': .01},
            'prisms': prisms or []}
    (root/'world.json').write_text(json.dumps(data))
    (root/'terrain.i16').write_bytes(array('h', [0] * 10201).tobytes())
    return World(directory)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_holes_preserve_courtyard(self):
        prism = Prism({'base': 0, 'top': 10, 'ring': rectangle(0, 0, 10, 10),
                       'holes': [rectangle(3, 3, 7, 7)]})
        self.assertFalse(prism.contains(5, 5))
        self.assertFalse(prism.touches(5, 5, 1))
        self.assertTrue(prism.touches(5, 5, 2.5))
        self.assertFalse(prism.blocks_ray([5, 4, 5], [5, 6, 5]))
        self.assertTrue(prism.blocks_ray([-2, 5, 5], [12, 5, 5]))

    def test_bridge_vertical_clearance(self):
        bridge = {'base': 12, 'top': 15, 'ring': rectangle(40, 40, 60, 60)}
        world = make_world(self.temp.name, [bridge])
        self.assertTrue(world.body_clear(50, 50, 0, 2.5, 9))
        self.assertFalse(world.body_clear(50, 50, 0, 2.5, 14))
        self.assertTrue(world.line_clear([30, 50, 7], [70, 50, 7]))
        self.assertFalse(world.line_clear([30, 50, 13], [70, 50, 13]))

    def test_terrain_bilinear_interpolation(self):
        world = make_world(self.temp.name)
        world.heights = array('h', [10 * x + 20 * y for y in range(101) for x in range(101)])
        self.assertAlmostEqual(world.height(1.25, 2.5), .625)

    def test_path_routes_around_wall(self):
        world = make_world(self.temp.name, [{'base': 0, 'top': 30, 'ring': rectangle(45, 2, 55, 70)}])
        self.assertFalse(world.walk_clear([13, 13, 0], [85, 13]))
        nav = Navigation(world, {'radius': 2.5, 'height': 9})
        path = nav.plan([13, 13, 0], [85, 13])
        self.assertLess(math.dist(path[-1], [85, 13]), 9)
        self.assertTrue(any(y > 70 for _, y in path))
        self.assertTrue(all(world.walk_clear([*a, 0], b, 2.5, 9) for a, b in zip(path, path[1:])))

    def test_can_leave_roof_without_falling_inside_wall(self):
        world = make_world(self.temp.name, [{'base': 0, 'top': 20, 'ring': rectangle(40, 40, 60, 60)}])
        self.assertTrue(world.walk_clear([50, 50, 20], [72, 50], radius=2.5))

    def test_planner_finds_a_mech_width_passage(self):
        world = make_world(self.temp.name, [
            {'base': 0, 'top': 30, 'ring': rectangle(0, 0, 45, 100)},
            {'base': 0, 'top': 30, 'ring': rectangle(52, 0, 100, 100)}])
        nav = Navigation(world, {'radius': 2.5, 'height': 9})
        path = nav.plan([49, 13, 0], [49, 85])
        self.assertTrue(path, 'A 7 m passage fits the 5 m mech')
        self.assertLess(math.dist(path[-1], [49, 85]), 5)

    def test_ray_does_not_skip_diagonal_bucket_corner(self):
        wall = {'base': 0, 'top': 10, 'ring': rectangle(23.6, 24.0, 23.95, 24.35)}
        world = make_world(self.temp.name, [wall])
        self.assertFalse(world.line_clear([20, 21, 5], [50, 46, 5]))


class PhysicsTests(unittest.TestCase):
    def test_intercepts_crossing_targets(self):
        randomizer = random.Random(1400)
        for _ in range(200):
            target = [randomizer.uniform(20, 180), randomizer.uniform(-80, 80), randomizer.uniform(-8, 12)]
            velocity = [randomizer.uniform(-19, 19), randomizer.uniform(-19, 19), 0]
            predicted, t = intercept([0, 0, 0], target, velocity, 252)
            self.assertAlmostEqual(math.dist([0, 0, 0], predicted), 252*t, places=6)
            self.assertAlmostEqual(math.dist(predicted, [target[i]+velocity[i]*t for i in range(3)]), 0, places=6)

    def test_cannot_intercept_faster_receding_target(self):
        self.assertIsNone(intercept([0, 0, 0], [100, 0, 0], [300, 0, 0], 252))

    def test_dodging_changes_predicted_bullet_risk(self):
        me = {'pos': [0, 0, 0], 'vel': [0, 0, 0]}
        bullet = [{'kind': 'gun', 'pos': [40, 0, 4.5], 'vel': [-100, 0, 0]}]
        still = projectile_danger(me, [0, 0], 0, bullet, 2.5, 9)
        dodge = projectile_danger(me, [0, 1], 19.5, bullet, 2.5, 9)
        self.assertLess(dodge, still)


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kit = Path(os.environ.get('MECHFIGHT_KIT', str(ROOT/'work/mechfight-kit')))
        if not kit.exists():
            raise RuntimeError('Run check_local.py first, or set MECHFIGHT_KIT to the unpacked official kit')
        with gzip.open(kit/'check/stream.jsonl.gz', 'rt') as stream:
            cls.messages = [json.loads(line) for line in stream]
        cls.hello = cls.messages[0]
        cls.hello['map']['dir'] = str(kit/'arena')
        cls.initial = next(m for m in cls.messages if m['type'] == 'obs')
        cls.prototype = Bot(cls.hello)

    def setUp(self):
        self.bot = copy.copy(self.prototype)
        self.obs = copy.deepcopy(self.initial)
        self.obs.update(t=10, mechs=[], projectiles=[], events=[], sounds=[], drops=[])
        self.bot.last_damage = -100
        self.bot.state = 'camp'

    def enemy(self, distance):
        pos = self.obs['self']['pos']
        return {'id': 4, 'kind': 'mech', 'pos': [pos[0]-distance, pos[1], pos[2]],
                'vel': [0, 0, 0], 'hp': 1400, 'max_hp': 1400, 'cubes': 0, 'aim': [0, 0], 'charging': False}

    def test_does_not_waste_gun_shots_at_extreme_range(self):
        enemy = self.enemy(200)
        action = self.bot.shooting(self.obs, [(enemy, 200, True)])
        self.assertEqual(action['fire'], [False, False])

    def test_shoots_isolated_enemy_before_it_reaches_close_range(self):
        self.open_world()
        enemy = self.enemy(100)
        self.obs['self']['aim'] = [math.pi, math.atan2(-2.5, 100)]
        self.assertTrue(self.bot.shooting(self.obs, [(enemy, 100, True)])['fire'][0])

    def test_finishes_wounded_enemy_instead_of_waiting_for_damage(self):
        self.open_world()
        enemy = self.enemy(120)
        enemy['hp'] = 40
        self.obs['self']['aim'] = [math.pi, math.atan2(-2.5, 120)]
        self.assertTrue(self.bot.shooting(self.obs, [(enemy, 120, True)])['fire'][0])

    def test_does_not_draw_an_idle_enemy_into_an_existing_fight(self):
        self.open_world()
        enemy = self.enemy(100)
        enemy['aim'] = [math.pi, 0]  # looking away from us
        self.obs['self']['aim'] = [math.pi, math.atan2(-2.5, 100)]
        self.assertFalse(self.bot.shooting(self.obs, [(enemy, 100, True)])['fire'][0])

    def test_arms_before_chasing_an_enemy_at_range(self):
        self.open_world()
        me = self.obs['self']
        enemy = self.enemy(110)
        self.obs['pickups'] = [{'id': 8001, 'kind': 'laser',
            'pos': [me['pos'][0]+20, me['pos'][1], me['pos'][2]]}]
        self.bot.choose_camp = lambda *args: tuple(me['pos'][:2])
        goal, state, _ = self.bot.objective(self.obs, [(enemy, 110, True)])
        self.assertEqual(state, 'loot')
        self.assertGreater(goal[0], me['pos'][0])

    def test_lightning_is_a_defense_instead_of_a_reason_to_charge_every_enemy(self):
        self.open_world()
        me = self.obs['self']
        enemy = self.enemy(65)
        me['weapons'][1] = {'kind': 'lightning', 'cooldown': 0,
            'stats': copy.deepcopy(self.hello['config']['weapons']['lightning'])}
        move, _, _ = self.bot.movement(self.obs, me['pos'][:2], 'fight', False, [(enemy, 65, True)])
        self.assertGreater(move[0], -.5)

    def test_healthy_bot_approaches_an_isolated_enemy(self):
        self.open_world()
        me = self.obs['self']
        enemy = self.enemy(110)
        self.bot.choose_camp = lambda *args: tuple(me['pos'][:2])
        goal, _, _ = self.bot.objective(self.obs, [(enemy, 110, True)])
        self.assertLess(math.dist(goal, enemy['pos'][:2]), 100)

    def test_takes_laser_instead_of_entering_fights_with_one_weapon(self):
        self.open_world()
        self.obs['pickups'] = [{'id': 8001, 'kind': 'laser', 'pos': self.obs['self']['pos']}]
        action = self.bot.act(self.obs)
        self.assertEqual(action.get('take'), 8001)

    def test_farms_accessible_crate_when_uncontested(self):
        self.open_world()
        me = self.obs['self']
        self.obs['pickups'] = []
        self.obs['crates'] = [{'id': 1001, 'pos': [me['pos'][0]-30, me['pos'][1], me['pos'][2]], 'hp': 300}]
        me['aim'] = [math.pi, math.atan2(1.2-7, 30)]
        self.assertTrue(self.bot.act(self.obs)['fire'][0])

    def test_holds_safe_range_instead_of_running_away_forever(self):
        self.open_world()
        me = self.obs['self']
        enemy = self.enemy(75)
        move, _, _ = self.bot.movement(self.obs, me['pos'][:2], 'fight', False, [(enemy, 75, True)])
        # A gun duel should strafe, not continually increase separation.
        self.assertGreater(abs(move[1]), .5)

    def test_defends_against_nearby_attacker(self):
        enemy = self.enemy(30)
        self.bot.last_damage = self.obs['t']
        self.obs['self']['aim'] = [math.pi, math.atan2(-2.5, 30)]
        self.bot.world = type('Open', (), {'line_clear': lambda *args: True})()
        action = self.bot.shooting(self.obs, [(enemy, 30, True)])
        self.assertTrue(action['fire'][0])

    def test_heals_instead_of_chasing_loot(self):
        self.obs['self']['hp'] *= .5
        self.obs['cubes'] = [{'id': 5001, 'pos': self.obs['self']['pos']}]
        self.bot.choose_camp = lambda *args: (0, 0)
        _, state, _ = self.bot.objective(self.obs, [])
        self.assertEqual(state, 'heal')

    def test_zone_escape_takes_priority_over_loot(self):
        self.obs['self']['in_zone'] = False
        self.obs['cubes'] = [{'id': 5001, 'pos': self.obs['self']['pos']}]
        self.bot.choose_camp = lambda *args: (0, 0)
        _, state, urgent = self.bot.objective(self.obs, [])
        self.assertEqual(state, 'zone')
        self.assertTrue(urgent)

    def test_blocked_memory_target_does_not_trigger_firing(self):
        enemy = self.enemy(25)
        self.bot.last_damage = self.obs['t']
        self.assertEqual(self.bot.shooting(self.obs, [(enemy, 25, False)])['fire'], [False, False])

    def test_repair_upgrade_beats_weapon_damage(self):
        offer = {'choices': [{'id': 'gun_damage'}, {'id': 'nanites'}, {'id': 'gun_rate'}]}
        self.assertEqual(self.bot.upgrade(offer, self.obs['self']), 1)

    def test_late_zone_prefers_immediate_health_over_regen(self):
        self.open_world()
        self.obs['t'] = 350
        self.obs['offer'] = {'choices': [{'id': 'nanites'}, {'id': 'plating'}, {'id': 'armor'}]}
        self.assertEqual(self.bot.act(self.obs)['pick'], 1)

    def test_gun_magazine_upgrade_beats_unneeded_sensors_or_turn_speed(self):
        offer = {'choices': [{'id': 'sensors'}, {'id': 'gun_mag'}, {'id': 'gyros'}]}
        self.assertEqual(self.bot.upgrade(offer, self.obs['self']), 1)

    def open_world(self):
        class OpenMap:
            def line_clear(self, a, b): return True
            def walk_clear(self, *args): return True
            def height(self, *args): return 0
            def cover_distance(self, *args): return 40
        class StraightNav:
            def direction(self, position, goal, *args):
                dx, dy = goal[0]-position[0], goal[1]-position[1]
                length = math.hypot(dx, dy)
                return (dx/length, dy/length) if length else (0, 0)
            def nearest(self, point, **kwargs): return tuple(point)
            def point(self, index): return index
        self.bot.world, self.bot.nav = OpenMap(), StraightNav()

    def test_reacts_to_incoming_bullet_while_camping(self):
        self.open_world()
        me = self.obs['self']
        self.obs['projectiles'] = [{'id': 1, 'kind': 'gun',
            'pos': [me['pos'][0]+40, me['pos'][1], me['pos'][2]+4.5], 'vel': [-100, 0, 0]}]
        move, boost, _ = self.bot.movement(self.obs, me['pos'][:2], 'camp', False, [])
        self.assertGreater(abs(move[1]), .7)
        self.assertTrue(boost)

    def test_reaches_cube_pickup_radius_instead_of_stopping_at_four_metres(self):
        self.open_world()
        me = self.obs['self']
        goal = [me['pos'][0]-3.8, me['pos'][1]]
        move, _, _ = self.bot.movement(self.obs, goal, 'cube', False, [])
        self.assertLess(move[0], -.5)

    def test_laser_charges_and_releases_when_ready_and_aligned(self):
        self.open_world()
        enemy = self.enemy(100)
        me = self.obs['self']
        me['aim'] = [math.pi, math.atan2(-2.5, 100)]
        laser = {'kind': 'laser', 'cooldown': 0, 'charge': 0,
                 'stats': copy.deepcopy(self.hello['config']['weapons']['laser'])}
        me['weapons'][1] = laser
        self.assertTrue(self.bot.shooting(self.obs, [(enemy, 100, True)])['fire'][1])
        laser['charge'] = .75
        self.assertFalse(self.bot.shooting(self.obs, [(enemy, 100, True)])['fire'][1])

    def test_gun_leads_a_moving_target_while_laser_is_still_charging(self):
        self.open_world()
        enemy = self.enemy(100)
        enemy['vel'] = [0, 15, 0]
        me = self.obs['self']
        me['weapons'][1] = {'kind': 'laser', 'cooldown': 0, 'charge': .25,
            'stats': copy.deepcopy(self.hello['config']['weapons']['laser'])}
        aim = self.bot.shooting(self.obs, [(enemy, 100, True)])['aim']
        self.assertGreater(abs((aim[0]-math.pi+math.pi)%math.tau-math.pi), .04)

    def test_does_not_waste_gun_ammo_when_aiming_an_instant_laser(self):
        self.open_world()
        enemy = self.enemy(100)
        enemy['vel'] = [0, 15, 0]
        me = self.obs['self']
        me['aim'] = [math.pi, math.atan2(-2.5, 100)]
        me['weapons'][1] = {'kind': 'laser', 'cooldown': 0, 'charge': .75,
            'stats': copy.deepcopy(self.hello['config']['weapons']['laser'])}
        action = self.bot.shooting(self.obs, [(enemy, 100, True)])
        self.assertFalse(action['fire'][0])
        self.assertFalse(action['fire'][1])  # releasing a charged beam fires it

    def test_never_swaps_a_charging_laser(self):
        self.open_world()
        me = self.obs['self']
        me['weapons'] = [
            {'kind': 'mortar', 'cooldown': 0, 'stats': self.hello['config']['weapons']['mortar']},
            {'kind': 'laser', 'charge': .4, 'cooldown': 0, 'stats': self.hello['config']['weapons']['laser']}]
        self.obs['pickups'] = [{'id': 8001, 'kind': 'lightning', 'pos': me['pos']}]
        action = self.bot.act(self.obs)
        self.assertEqual(action.get('drop'), 0)

    def test_will_not_choose_a_mortar_it_cannot_use(self):
        self.open_world()
        enemy = self.enemy(100)
        me = self.obs['self']
        me['weapons'][1] = {'kind': 'mortar', 'cooldown': 0,
            'stats': copy.deepcopy(self.hello['config']['weapons']['mortar'])}
        # 72 m/s and g=22: low arc to (100,0,-2.5) is about 0.19 rad.
        me['aim'] = [math.pi, .19]
        action = self.bot.shooting(self.obs, [(enemy, 100, True)])
        self.assertTrue(action['fire'][1])

    def test_mortar_aim_accounts_for_gravity_and_tube_height(self):
        self.open_world()
        enemy = self.enemy(100)
        me = self.obs['self']
        me['weapons'][1] = {'kind': 'mortar', 'cooldown': 0,
            'stats': copy.deepcopy(self.hello['config']['weapons']['mortar'])}
        pitch = self.bot.shooting(self.obs, [(enemy, 100, True)])['aim'][1]
        # Independent trajectory calculation: target center is 5 m below tube.
        flight = 100/(72*math.cos(pitch))
        self.assertAlmostEqual(72*math.sin(pitch)*flight-11*flight*flight, -5, delta=.1)

    def test_recovers_to_a_useful_health_level_before_rejoining_combat(self):
        self.open_world()
        me = self.obs['self']
        me['hp'] = me['max_hp']*.4
        self.bot.choose_camp = lambda *args: tuple(me['pos'][:2])
        self.assertEqual(self.bot.objective(self.obs, [])[1], 'heal')
        me['hp'] = me['max_hp']*.6
        enemy = self.enemy(100)
        self.assertEqual(self.bot.objective(self.obs, [(enemy, 100, True)])[1], 'heal')

    def test_steps_out_of_charging_laser_line(self):
        self.open_world()
        me = self.obs['self']
        enemy = self.enemy(40)
        enemy['charging'], enemy['aim'] = True, [0, 0]
        move, boost, _ = self.bot.movement(self.obs, me['pos'][:2], 'camp', False, [(enemy, 40, True)])
        self.assertGreater(abs(move[1]), .7)

    def test_energy_recovers_before_another_boost_burst(self):
        self.open_world()
        me = self.obs['self']
        goal = [me['pos'][0]+100, me['pos'][1]]
        me['energy'] = 10
        self.assertFalse(self.bot.movement(self.obs, goal, 'zone', True, [])[1])
        me['energy'] = 50
        self.assertFalse(self.bot.movement(self.obs, goal, 'zone', True, [])[1])
        me['energy'] = 80
        self.assertTrue(self.bot.movement(self.obs, goal, 'zone', True, [])[1])

    def test_visible_head_is_still_a_threat(self):
        enemy = self.enemy(40)
        foot = enemy['pos'][2]
        self.bot.world = type('LowWall', (), {'line_clear': lambda _, a, b: b[2] > foot+7})()
        self.assertTrue(self.bot.visibility(self.obs['self'], [enemy])[0][2])

    def test_can_shoot_an_exposed_head_above_low_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bot.world = make_world(directory, [
                {'base': 0, 'top': 6, 'ring': rectangle(45, 40, 55, 60)}])
            me = self.obs['self']
            me['pos'] = [25, 50, 0]
            me['aim'] = [0, math.atan2(1.1, 50)]
            enemy = self.enemy(50)
            enemy['pos'] = [75, 50, 0]
            enemy['aim'] = [math.pi, 0]
            action = self.bot.shooting(self.obs, [(enemy, 50, True)])
            self.assertTrue(action['fire'][0])
            self.assertGreater(action['aim'][1], 0)

    def test_stays_nearby_when_already_safely_hidden(self):
        self.open_world()
        self.obs['self']['pos'] = [0, 0, 0]
        self.obs['zone'].update(center=[0, 0], radius=560, next_center=[100, 0], next_radius=380)
        self.assertLess(math.dist(self.bot.choose_camp(self.obs, []), [0, 0]), 1)

    def test_defends_against_drone_instead_of_ignoring_it(self):
        self.open_world()
        enemy = self.enemy(30)
        enemy.update(id=101, kind='npc', hp=350, max_hp=350)
        self.obs.update(mechs=[enemy], pickups=[], events=[{'type': 'damaged', 'amount': 25, 'by': 101, 'bearing': math.pi}])
        self.obs['self']['aim'] = [math.pi, math.atan2(1.75-7, 30)]
        self.bot.choose_camp = lambda *args: (self.obs['self']['pos'][0]+30, self.obs['self']['pos'][1])
        self.assertTrue(self.bot.act(self.obs)['fire'][0])

    def test_replay_has_no_exceptions_or_nonfinite_actions(self):
        bot = Bot(self.hello)
        for observation in self.messages:
            if observation['type'] != 'obs':
                continue
            action = bot.act(observation)
            json.dumps(action, allow_nan=False)
            self.assertEqual(action['tick'], observation['tick'])
            self.assertLessEqual(math.hypot(*action['move']), 1.000001)


if __name__ == '__main__':
    unittest.main()
