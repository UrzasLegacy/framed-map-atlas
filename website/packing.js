"use strict";
/* Shared by the gallery and the offline importer. Java 26.3 console syntax. */
const Packing = (() => {
  const quote = value => JSON.stringify(String(value));
  const text = value => "{text:" + quote(value) + ",italic:false}";
  function mapItem(t, layout, remap) {
    const id = remap ? remap[t.id] : t.id;
    if (!Number.isInteger(id) || id < 0 || id > 2147483647) throw Error("Invalid destination map ID");
    const components = ["map_id:" + id];
    if (t.name) components.push("custom_name:" + text(t.name));
    if (layout) {
      let label = "R" + t.row + " C" + t.col;
      if (t.turn) label += " · " + (t.turn*90) + "° clockwise";
      components.push("lore:[" + text(label) + "]");
    }
    return '{id:"filled_map",components:{' + components.join(",") + "}}";
  }
  function pack(selections, player, remap) {
    if (!/^[A-Za-z0-9_]{1,16}$/.test(player)) throw Error("Enter an online Java player name (1–16 letters, digits or underscores).");
    const bundles = [];
    for (const item of selections) {
      const count = Math.ceil(item.tiles.length / 64);
      for (let part=0; part<count; part++) {
        const tiles = item.tiles.slice(part*64,(part+1)*64);
        const label = item.title || tiles.find(t=>t.name)?.name || ("Map #" + tiles[0].id);
        const name = label + (count>1 ? " (" + (part+1) + "/" + count + ")" : "");
        const contents = "[" + tiles.map(t=>mapItem(t,item.kind==="art",remap)).join(",") + "]";
        const stack = '{id:"bundle",components:{custom_name:' + text(name) + ",bundle_contents:" + contents + "}}";
        bundles.push({name, stack, count:tiles.length});
      }
    }
    // Keep bundle boundaries intact. A smaller box also fits console front ends with a 32 KiB line limit.
    const boxes = [];
    const bytes = value => new TextEncoder().encode(value).length;
    let slots = [], length = 0;
    function prefix() {
      return "minecraft:give " + player + " minecraft:shulker_box[custom_name=" +
        text("Wubcraft map art · Box " + (boxes.length+1)) + ",container=[";
    }
    function finish() {
      boxes.push(prefix() + slots.join(",") + "]] 1");
      slots = []; length = 0;
    }
    for (const bundle of bundles) {
      const slot = n => "{slot:" + n + ",item:" + bundle.stack + "}";
      let entry = slot(slots.length);
      if (slots.length && (slots.length===27 || bytes(prefix())+length+bytes(entry)+6 > 30000)) {
        finish();
        entry = slot(0);
      }
      if (bytes(prefix())+bytes(entry)+4 > 30000) throw Error("A named bundle exceeds the console command budget.");
      slots.push(entry);
      length += bytes(entry)+1;
    }
    if (slots.length) finish();
    return {commands:boxes, bundles:bundles.length, placements:bundles.reduce((n,b)=>n+b.count,0)};
  }
  return {pack};
})();
if (typeof module !== "undefined") module.exports = Packing;

