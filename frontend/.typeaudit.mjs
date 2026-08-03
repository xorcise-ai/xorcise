import { chromium } from '@playwright/test';
const B='http://127.0.0.1:3001/ui';
const R=[['dashboard','/'],['agents','/agents/'],['missions','/missions/'],['runs','/runs/'],
         ['results','/results/'],['settings','/settings/'],['setup','/setup/'],['new','/runs/new/']];
const b=await chromium.launch();
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage();
const size={}, track={}, lhByFs={}, upper={}, weights={};
let total=0, prose=[];
for(const [n,r] of R){
  await p.goto(B+r,{waitUntil:'networkidle',timeout:25000}); await p.waitForTimeout(1100);
  const d=await p.evaluate(()=>{
    const out={size:{},track:{},lh:{},upper:{},w:{},total:0,prose:[]};
    document.querySelectorAll('body *').forEach(e=>{
      const rect=e.getBoundingClientRect(); if(rect.width===0||rect.height===0)return;
      const dn=[...e.childNodes].filter(x=>x.nodeType===3&&x.textContent.trim().length>1);
      if(!dn.length)return;
      const s=getComputedStyle(e), fs=Math.round(parseFloat(s.fontSize)*10)/10;
      out.total++;
      out.size[fs]=(out.size[fs]||0)+1;
      out.w[s.fontWeight]=(out.w[s.fontWeight]||0)+1;
      const ls=s.letterSpacing==='normal'?0:+(parseFloat(s.letterSpacing)/fs).toFixed(3);
      const isUp=s.textTransform==='uppercase';
      if(isUp){ out.upper[fs+'px@'+ls+'em']=(out.upper[fs+'px@'+ls+'em']||0)+1; }
      out.track[ls]=(out.track[ls]||0)+1;
      const key=fs+'px'; const lh=+(parseFloat(s.lineHeight)/fs).toFixed(2);
      out.lh[key]=out.lh[key]||{}; out.lh[key][lh]=(out.lh[key][lh]||0)+1;
      const t=dn.map(x=>x.textContent.trim()).join(' ');
      if(t.length>110) out.prose.push({fs,lh,ch:Math.round(rect.width/(fs*0.6))});
    });
    return out;
  });
  total+=d.total;
  for(const k in d.size) size[k]=(size[k]||0)+d.size[k];
  for(const k in d.track) track[k]=(track[k]||0)+d.track[k];
  for(const k in d.upper) upper[k]=(upper[k]||0)+d.upper[k];
  for(const k in d.w) weights[k]=(weights[k]||0)+d.w[k];
  for(const k in d.lh){ lhByFs[k]=lhByFs[k]||{}; for(const j in d.lh[k]) lhByFs[k][j]=(lhByFs[k][j]||0)+d.lh[k][j]; }
  prose.push(...d.prose);
}
const srt=o=>Object.entries(o).sort((a,b)=>parseFloat(a[0])-parseFloat(b[0]));
console.log('TOTAL text elements:',total);
console.log('\n=== SIZE DISTRIBUTION (brand scale: 9-10 label / 11 caption / 12 data / 14 body / 18-20 lead) ===');
srt(size).forEach(([k,v])=>{
  const ok=[9,10,11,12,14,18,20].includes(parseFloat(k));
  console.log(`  ${k.padStart(5)}px  ${String(v).padStart(4)}  ${ok?'':'  <-- OFF-SCALE'}`);
});
console.log('\n=== TRACKING (brand: labels/eyebrows 0.14-0.28em, body 0) ===');
srt(track).forEach(([k,v])=>console.log(`  ${k.padStart(7)}em ${String(v).padStart(4)}`));
console.log('\n=== UPPERCASE runs: size@tracking (these MUST be 0.14-0.28em) ===');
Object.entries(upper).sort((a,b)=>b[1]-a[1]).forEach(([k,v])=>{
  const em=parseFloat(k.split('@')[1]);
  console.log(`  ${k.padEnd(18)} ${String(v).padStart(4)}  ${em>=0.14?'ok':'<-- UNDER-TRACKED'}`);
});
console.log('\n=== LINE-HEIGHT by size (brand: body 1.6 / prose 1.7 / code 1.5 / rows 1.45) ===');
srt(lhByFs).forEach(([k,v])=>console.log(`  ${k.padStart(6)}: ${Object.entries(v).map(([a,b])=>a+'×'+b).join('  ')}`));
console.log('\n=== WEIGHTS ===', JSON.stringify(weights));
const bad=prose.filter(x=>x.ch>75||x.lh<1.6);
console.log(`\n=== PROSE: ${prose.length} blocks, ${bad.length} still over 75ch or under 1.6 lh ===`);
bad.slice(0,8).forEach(x=>console.log(`  ${x.fs}px lh${x.lh} ${x.ch}ch`));
await b.close();
