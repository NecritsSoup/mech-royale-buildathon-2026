# Mech Royale revision notes

## Why the original strategy was weak

The official practice rules use a squad of 15 house bots that share vision and
do not fight each other. Waiting for them to eliminate one another cannot work.
The shrinking circle also eventually removes room to disengage. Practice seeds
are fixed throughout the event, so submitting identical code repeatedly is a
poor substitute for improving the controller, especially with ample timing margin.

The submitted v1 is GitHub commit
`cd83f0abe5063a7eb4ccb08e7dcfb53c165cc97e`. Its source matches the frozen local
baseline in `work/v1`. Code inspection and local matches found:

- The gun generally waited for enemies to approach within 40 m or deal damage.
- The bot only sought lightning and did not deliberately break crates for loot.
- Movement could stop 3.8 m from a cube, outside its 3.5 m pickup radius.
- An 8 m navigation grid could miss passages a 5 m wide mech can use.
- Charging a laser caused shared aim to track the current target position,
  wasting gun shots that needed projectile lead.

## Public approaches reviewed

Leaderboard names could not be reliably matched to public GitHub accounts.
These are relevant public entrants, not verified claims about who owns the
current leading scores:

| Source | Useful ideas to examine |
| --- | --- |
| [Anthony Teti](https://github.com/anthonyteti/buildathon-mech-royale) | Navigation testing and paired match comparisons rather than recorded observations alone; multiple weapons, loot, recovery. |
| [Kyt0V0 / ironbuckeye](https://github.com/Kyt0V0/Mech-Royale) | Finer navigation, weapon range control, cover, gearing and a local squad emulator. |
| [Not-Ethan / oval-ghost](https://github.com/Not-Ethan/oval-ghost-mech) | Selective engagement, recovery hysteresis, early safe resources, cover during reloads, and prioritizing HP in the late zone. |

The Python controller changes implement these general concepts in this entry's
existing geometry and protocol framework. The external simulator is the only
new third-party executable development dependency. Its source and MIT license
are retained together in ignored `work/research/`.

## Validation method

Official `check/check.py` sends 300 recorded observations. It checks protocol
conformance and timing; its stream does not react to actions. Regression tests
add independent geometry and behavior fixtures.

The local match approximation is from Anthony Teti's repository, pinned to
`fc98e18b67e4f2159224d46db3755a20bd56fb24`. `compare_local.mjs` calls that
simulator with our original and revised Python processes on paired seeds. It
includes combat, terrain, loot, a shrinking zone and an emulated house squad.
The actual house controllers are unknown. This simulator cannot establish an
official practice score, final rank or win rate.

Exploratory versions are frozen in separate ignored folders before each run.
Aggressive variants that did more damage but died sooner were rejected.
Fresh seeds are reserved for a final comparison after choosing a candidate.

The final candidate (including the exposed-head firing fix) was then compared
on 24 new paired seeds, 201–224:

| Local measure | Submitted v1 | Final revision |
| --- | ---: | ---: |
| Mean placement points | 0.917 | 1.542 |
| Mean mech eliminations | 0.208 | 0.833 |

The mean paired score gain was 0.625, with standard error 0.403. This small
sample suggests improvement but does not establish a reliable gain; the actual
practice server remains the decisive test. Results are in the ignored local
`work/holdout-final.json` file. The final version also passes all 43 regression
tests and answers all 300 official checker observations within the time limit
(local maximum 6.1 ms); the synthetic dense timing maximum was 6.17 ms.

## Reproduce a local comparison

Use Python 3.13 and Node 22.18 or later. Obtain the pinned simulator separately,
retaining its MIT license. Prepare the official kit, a JSON file containing
`hello.config`, and an opponent registry:

```json
{
  "v1": {"cwd": "/absolute/path/to/original", "cmd": ["python3", "-u", "bot.py"]},
  "v2": {"cwd": "/absolute/path/to/revision", "cmd": ["python3", "-u", "bot.py"]}
}
```

Set `KIT` to the unpacked official kit directory, `CFG` to that config JSON,
`OPP_REGISTRY` to the registry, and `EXT_TIMEOUT_MS=50`. Then run:

```sh
node compare_local.mjs /absolute/path/to/simulator/sim/sim.ts 32 101 v1 v2
```

An optional `RESULT_FILE` path saves every paired result and its summary as JSON.
The simulator and its approximate house bots are not included in the image.
