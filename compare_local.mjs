/** Local A/B adapter for Anthony Teti's MIT-licensed approximate simulator.
 * Usage: KIT=... CFG=... OPP_REGISTRY=... node compare_local.mjs /path/to/sim/sim.ts 12 1 v1 v2
 * Opponent registry names select our frozen Python versions, not other entrants.
 */
import { pathToFileURL } from 'node:url';
import { writeFileSync } from 'node:fs';

const [simulator, nText='12', seedText='1', baseline='v1', candidate='v2'] = process.argv.slice(2);
const {runMatch} = await import(pathToFileURL(simulator).href);
const n=Number(nText), seed0=Number(seedText), rows=[];
for(let i=0;i<n;i++) {
  const pair=[];
  // Both controllers see the same seed, spawn, world and approximate squad.
  for(const name of [baseline,candidate]) {
    const out=await runMatch({seed:seed0+i,mode:'practice',ours:[{ext:name,label:name}],maxT:480});
    const r=out.results.find(x=>x.label===name);
    if(!r) throw new Error('Missing match result: '+name);
    pair.push({...r,score:16-r.place});
  }
  rows.push({seed:seed0+i,baseline:pair[0],candidate:pair[1]});
  console.log(JSON.stringify({seed:seed0+i,A:pair[0].score,B:pair[1].score,
    deathA:pair[0].deathT,deathB:pair[1].deathT,causeB:pair[1].cause}));
}
const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
const delta=rows.map(r=>r.candidate.score-r.baseline.score), gain=mean(delta);
const se=Math.sqrt(delta.reduce((s,x)=>s+(x-gain)**2,0)/Math.max(1,n-1)/n);
const summary={matches:n,seed0,baseline,candidate,
  meanA:mean(rows.map(r=>r.baseline.score)),meanB:mean(rows.map(r=>r.candidate.score)),
  paired_gain:gain,standard_error:se,
  killsA:mean(rows.map(r=>r.baseline.kills)),killsB:mean(rows.map(r=>r.candidate.kills)),
  note:'Approximate local simulator; these are not official practice scores.'};
if(process.env.RESULT_FILE) writeFileSync(process.env.RESULT_FILE,JSON.stringify({summary,rows},null,2));
console.log(JSON.stringify(summary,null,2));
