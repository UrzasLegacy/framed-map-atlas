const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {createHash} = require("node:crypto");
const {stage} = require("./import-world.cjs");

test("stages fixed IDs, skips installed copies and leaves the counter alone", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gallery-fixed-"));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  const maps = path.join(root, "world/data/minecraft/maps");
  const dats = path.join(root, "dats"), name = "100000.dat";
  fs.mkdirSync(maps, {recursive:true});
  fs.mkdirSync(dats);
  const bytes = Buffer.from("fixture");
  fs.writeFileSync(path.join(dats, name), bytes);
  const fileHash = createHash("sha256").update(bytes).digest("hex");
  const spec = {format:"wubcraft-map-selection-v1", files:{100000:fileHash},
    selections:[{title:"Fern",kind:"single",tiles:[{id:100000,name:"Fern"}]}]};
  fs.writeFileSync(path.join(root, "selection.json"), JSON.stringify(spec));
  const run = folder => stage(root, maps, path.join(root,folder), "Player");
  run("first"); // The world need not have a counter file yet.
  assert.deepEqual(fs.readdirSync(path.join(root,"first/maps")), [name]);
  assert.match(fs.readFileSync(path.join(root,"first/commands.txt"),"utf8"), /map_id:100000\b/);
  fs.writeFileSync(path.join(maps,"last_id.dat"), "counter fixture");
  fs.copyFileSync(path.join(dats,name), path.join(maps,name));
  run("second");
  assert.deepEqual(fs.readdirSync(path.join(root,"second/maps")), []);
  fs.writeFileSync(path.join(maps,name), "other artwork");
  assert.throws(() => run("collision"), /100000 is occupied/);
  assert.equal(fs.existsSync(path.join(root,"collision")), false);
  assert.equal(fs.readFileSync(path.join(maps,name),"utf8"), "other artwork");
  assert.equal(fs.readFileSync(path.join(maps,"last_id.dat"),"utf8"), "counter fixture");
});
