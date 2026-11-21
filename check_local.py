"""Run the official recorded-stream checker; optional dense-observation timing stress."""
import argparse
import copy
import gzip
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import time

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--kit', type=Path)
parser.add_argument('--stress', action='store_true')
args = parser.parse_args()
kit = args.kit
if kit is not None:
    kit = kit.resolve()
if kit is None:
    kit = root / 'work/mechfight-kit'
    if not kit.exists():
        archive = root.parent / 'kits/mech-royale/mechfight-kit.tar.gz'
        if not archive.exists():
            raise SystemExit('Pass --kit with the unpacked official mechfight-kit folder')
        (root / 'work').mkdir(exist_ok=True)
        with tarfile.open(archive) as stream:
            stream.extractall(root / 'work', filter='data')
subprocess.run([sys.executable, str(kit/'check/check.py'), '--', sys.executable, '-u', str(root/'bot.py')], check=True)
if args.stress:
    (root / 'work').mkdir(exist_ok=True)
    from bot import Bot
    with gzip.open(kit/'check/stream.jsonl.gz', 'rt') as stream:
        messages = [json.loads(line) for line in stream]
    hello = messages[0]
    hello['map']['dir'] = str(kit/'arena')
    bot = Bot(hello)
    base = next(message for message in messages if message['type'] == 'obs')
    durations = []
    states = {}
    for tick in range(100):
        observation = copy.deepcopy(base)
        observation['t'], observation['tick'] = tick*.1, tick*2
        me = observation['self']
        observation['mechs'] = []
        for i in range(15):
            angle = i*math.tau/15
            radius = 45+i*5
            x = max(bot.world.bounds['minX']+5, min(bot.world.bounds['maxX']-5, me['pos'][0]+radius*math.cos(angle)))
            y = max(bot.world.bounds['minY']+5, min(bot.world.bounds['maxY']-5, me['pos'][1]+radius*math.sin(angle)))
            observation['mechs'].append({'id': i if i < me['id'] else i+1, 'kind': 'mech', 'pos': [x, y, bot.world.height(x, y)],
                'vel': [4*math.sin(angle), 4*math.cos(angle), 0], 'aim': [angle+math.pi, 0],
                'hp': 1400, 'max_hp': 1400, 'cubes': i%5, 'charging': i%3==0, 'weapons': ['laser']})
        observation['projectiles'] = [{'id': i, 'kind': 'gun',
            'pos': [me['pos'][0]+(30+i%10)*math.cos(i), me['pos'][1]+(30+i%10)*math.sin(i), me['pos'][2]+4.5],
            'vel': [-252*math.cos(i), -252*math.sin(i), 0]} for i in range(120)]
        start = time.perf_counter()
        action = bot.act(observation)
        durations.append((time.perf_counter()-start)*1000)
        json.dumps(action, allow_nan=False)
        states[bot.state] = states.get(bot.state, 0)+1
    result = {'dense_observations': len(durations), 'p50_ms': statistics.median(durations),
              'p99_ms': sorted(durations)[98], 'max_ms': max(durations), 'states': states,
              'note': 'Synthetic timing stress, not a simulation or placement score'}
    (root/'work/stress-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
