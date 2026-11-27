"use strict";
// Stage files for a stopped Java 26.3 save. All writes are under the new output folder.
const fs = require("node:fs"), path = require("node:path");
const crypto = require("node:crypto");
const {pack} = require("./packing.js");
const sha = b => crypto.createHash("sha256").update(b).digest("hex");
const read = f => fs.readFileSync(f);
const json = f => JSON.parse(read(f));
function stage(packageDir, mapsDir, output, player) {
  const spec=json(path.join(packageDir,"selection.json"));
  if(spec.format!=="wubcraft-map-selection-v1") throw Error("Unknown selection format");
  if(path.basename(mapsDir)!=="maps" || path.basename(path.dirname(mapsDir))!=="minecraft")
    throw Error("Choose the Java 26.3 data/minecraft/maps directory.");
  if (!fs.statSync(mapsDir).isDirectory()) throw Error("Choose a map directory.");
  const additions=[];
  const ids=new Set(spec.selections.flatMap(s=>s.tiles.map(t=>t.id)));
  for(const id of ids) {
    if(!Number.isSafeInteger(id) || id<100000 || id>2147483647) throw Error("Invalid source map ID");
    const bytes=read(path.join(packageDir,"dats",id+".dat"));
    const digest=sha(bytes);
    if(spec.files[id]!==digest) throw Error("DAT checksum mismatch for "+id);
    const target=path.join(mapsDir,id+".dat");
    if(fs.existsSync(target)) {
      if(sha(read(target))!==digest) throw Error("Map ID "+id+" is occupied by a different DAT. Keep the existing file.");
    } else {
      additions.push([id,bytes]);
    }
  }
  const result=pack(spec.selections,player);
  if(fs.existsSync(output)) throw Error("Choose a fresh output folder");
  fs.mkdirSync(path.dirname(output),{recursive:true});
  const tmp=fs.mkdtempSync(path.join(path.dirname(output),".gallery-stage-"));
  try {
    const maps=path.join(tmp,"maps"); fs.mkdirSync(maps);
    for(const [id,bytes] of additions) fs.writeFileSync(path.join(maps,id+".dat"),bytes);
    fs.writeFileSync(path.join(tmp,"commands.txt"),result.commands.join("\n")+"\n");
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
module.exports={stage};

