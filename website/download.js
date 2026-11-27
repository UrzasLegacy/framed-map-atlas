"use strict";
const Downloads = (() => {
  const encoder = new TextEncoder();
  const table = Uint32Array.from({length:256},(_,i)=>{
    for(let j=0;j<8;j++) i=i&1 ? (i>>>1)^0xedb88320 : i>>>1;
    return i>>>0;
  });
  function zip(files) {
    const parts=[], directory=[];
    let offset=0, centralSize=0;
    for (const [path,bytes] of files) {
      const name=encoder.encode(path), n=bytes.length;
      let crc=0xffffffff;
      for(const b of bytes) crc=table[(crc^b)&255]^(crc>>>8);
      crc=(crc^0xffffffff)>>>0;
      function header(size) {
        const buf=new Uint8Array(size+name.length);
        buf.set(name,size);
        return [buf,new DataView(buf.buffer)];
      }
      const [local,l]=header(30), [central,c]=header(46);
      l.setUint32(0,0x04034b50,true); l.setUint16(4,20,true);
      l.setUint16(12,33,true); l.setUint32(14,crc,true);
      l.setUint32(18,n,true); l.setUint32(22,n,true); l.setUint16(26,name.length,true);
      c.setUint32(0,0x02014b50,true); c.setUint16(4,20,true); c.setUint16(6,20,true);
      c.setUint16(14,33,true); c.setUint32(16,crc,true);
      c.setUint32(20,n,true); c.setUint32(24,n,true); c.setUint16(28,name.length,true); c.setUint32(42,offset,true);
      parts.push(local,bytes); directory.push(central);
      offset+=local.length+n; centralSize+=central.length;
    }
    if(files.length>65535 || offset+centralSize>=2**32) throw Error("Split this download into smaller selections.");
    const end=new Uint8Array(22), v=new DataView(end.buffer);
    v.setUint32(0,0x06054b50,true); v.setUint16(8,files.length,true); v.setUint16(10,files.length,true);
    v.setUint32(12,centralSize,true); v.setUint32(16,offset,true);
    return new Blob([...parts,...directory,end],{type:"application/zip"});
  }
  async function bytes(url) {
    const r=await fetch(url);
    if(!r.ok) throw Error("Could not load "+url+" ("+r.status+")");
    return new Uint8Array(await r.arrayBuffer());
  }
  function save(blob,name) {
    const url=URL.createObjectURL(blob), a=document.createElement("a");
    a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  }
  async function portable(selections, catalog, progress) {
    if(location.protocol==="file:") throw Error("For world downloads, open the hosted gallery or serve this folder with python -m http.server.");
    const ids=new Set(selections.flatMap(s=>s.tiles.map(t=>t.id)));
    const blocks=new Map(), hashes={};
    for(const id of ids) {
      const [block,start,length,sha]=catalog.maps[id];
      if(!blocks.has(block)) blocks.set(block,[]);
      blocks.get(block).push({id,start,length}); hashes[id]=sha;
    }
    const files=[];
    for(const name of ["import-world.cjs","packing.js","IMPORT.md"]) files.push([name,await bytes(name)]);
    files.push(["selection.json",encoder.encode(JSON.stringify({format:"wubcraft-map-selection-v1",files:hashes,selections},null,2))]);
    let done=0;
    for(const [block,rows] of blocks) {
      const raw=await bytes("collection/maps/"+block+".bin");
      for(const {id,start,length} of rows) {
        if(start+length>raw.length) throw Error("Truncated map download for "+id);
        files.push(["dats/"+id+".dat",raw.subarray(start,start+length)]);
      }
      done+=rows.length; progress(done,ids.size);
    }
    return zip(files);
  }
  return {save, portable, zip};
})();
if(typeof module!=="undefined") module.exports=Downloads;

