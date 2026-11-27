"use strict";
// Stage files for a stopped Java 26.3 save. All writes are under the new output folder.
const fs = require("node:fs"), path = require("node:path"), zlib = require("node:zlib");
const crypto = require("node:crypto");
const {pack} = require("./packing.js");
const sha = b => crypto.createHash("sha256").update(b).digest("hex");
const read = f => fs.readFileSync(f);
const json = f => JSON.parse(read(f));
function rawNBT(b) {
  return b[0]===31 && b[1]===139 ? zlib.gunzipSync(b,{maxOutputLength:64*1024*1024}) : b;
}
// The counter is an NBT root compound with DataVersion and a data compound containing map.
function counter(bytes) {
  const raw = rawNBT(bytes);
  let p=0, offset=null, end=null;
  function name() {const n=raw.readUInt16BE(p); p+=2; const s=raw.toString("utf8",p,p+n); p+=n; return s;}
  function compound(keys) {
    if (keys.length>8) throw Error("Unrecognized counter nesting");
    while (true) {
      const type=raw.readUInt8(p++);
      if (!type) { if (keys.join("/")==="data") end=p-1; return; }
      const field=name();
      if (type===10) compound([...keys,field]);
      else if (type===3) {
        if (keys.join("/")==="data" && field==="map") offset=p;
        raw.readInt32BE(p); p+=4;
      } else throw Error("Unexpected map counter format. Keep the original counter.");
    }
  }
  if (raw.readUInt8(p++)!==10) throw Error("Invalid map counter");
  name(); compound([]);
  if (end===null || p!==raw.length) throw Error("Incomplete map counter");
  return {last:offset===null?-1:raw.readInt32BE(offset), encode(value) {
    if(offset!==null) {const updated=Buffer.from(raw); updated.writeInt32BE(value,offset); return zlib.gzipSync(updated);}
    const field=Buffer.from("0300036d617000000000","hex"); field.writeInt32BE(value,6);
    return zlib.gzipSync(Buffer.concat([raw.subarray(0,end),field,raw.subarray(end)]));
  }};
}
function stage(packageDir, mapsDir, output, player) {
  const spec=json(path.join(packageDir,"selection.json"));
  if(spec.format!=="wubcraft-map-selection-v1") throw Error("Unknown selection format");
  if(path.basename(mapsDir)!=="maps" || path.basename(path.dirname(mapsDir))!=="minecraft")
    throw Error("Choose the Java 26.3 data/minecraft/maps directory.");
  const entries=fs.readdirSync(mapsDir);
  const lastFile=path.join(mapsDir,"last_id.dat");
  if(!fs.existsSync(lastFile)) throw Error("Create and use one vanilla map, save and close the world, then choose its maps directory.");
  const state=counter(read(lastFile));
  let last=entries.reduce((n,s)=>/^\d+\.dat$/.test(s)?Math.max(n,Number(s.slice(0,-4))):n,state.last);
  const bookName="wubcraft-gallery-ids.json";
  const existing=path.join(mapsDir,bookName);
  const ledger=fs.existsSync(existing)?json(existing):{};
  const remap={}, additions=[];
  const ids=new Set(spec.selections.flatMap(s=>s.tiles.map(t=>t.id)));
  for(const id of ids) {
    if(!Number.isSafeInteger(id) || id<0) throw Error("Invalid source map ID");
    const bytes=read(path.join(packageDir,"dats",id+".dat"));
    const digest=sha(bytes);
    if(spec.files[id]!==digest) throw Error("DAT checksum mismatch for "+id);
    const old=ledger[digest];
    if(Number.isInteger(old) && old>=0 && old<=2147483647 && fs.existsSync(path.join(mapsDir,old+".dat")) &&
       sha(read(path.join(mapsDir,old+".dat")))===digest) remap[id]=old;
    else {
      if(++last>2147483647) throw Error("Destination map IDs exhausted");
      remap[id]=last; ledger[digest]=last; additions.push([last,bytes]);
    }
  }
  const result=pack(spec.selections,player,remap);
  if(fs.existsSync(output)) throw Error("Choose a fresh output folder");
  fs.mkdirSync(path.dirname(output),{recursive:true});
  const tmp=fs.mkdtempSync(path.join(path.dirname(output),".gallery-stage-"));
  try {
    const maps=path.join(tmp,"maps"); fs.mkdirSync(maps);
    for(const [id,bytes] of additions) fs.writeFileSync(path.join(maps,id+".dat"),bytes);
    fs.writeFileSync(path.join(maps,bookName),JSON.stringify(ledger,null,2));
    fs.writeFileSync(path.join(maps,"last_id.dat"),state.encode(last));
    fs.writeFileSync(path.join(tmp,"commands.txt"),result.commands.join("\n")+"\n");
    fs.writeFileSync(path.join(tmp,"mapping.json"),JSON.stringify(remap,null,2));
    fs.renameSync(tmp,output);
  } finally { if(fs.existsSync(tmp)) fs.rmSync(tmp,{recursive:true}); }
  console.log(additions.length+" new DATs; "+result.commands.length+" shulker commands. Output: "+output);
}
if(require.main===module) {
  try {
    const [maps,out,player]=process.argv.slice(2);
    if(!maps || !out || !player) throw Error('Usage: node import-world.cjs "path/to/data/minecraft/maps" "new-output-folder" PlayerName');
    stage(__dirname,path.resolve(maps),path.resolve(out),player);
  } catch(e) {console.error(e.message);process.exitCode=1;}
}
module.exports={stage,counter};

