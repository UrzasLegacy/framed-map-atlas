"use strict";
(() => {
  const $=id=>document.getElementById(id);
  const catalog=window.ATLAS;
  if(!catalog) {$("results").textContent="Collection missing. Build the gallery from the migration export.";return;}
  const cards=catalog.cards, byKey=new Map(cards.map(c=>[c.key,c]));
  const signature=(id,name)=>JSON.stringify([id,name]);
  const singles=new Map(cards.filter(c=>c.kind==="single").map(c=>[signature(c.tiles[0].id,c.tiles[0].name),c]));
  const originals=new Map();
  for(const [id,name,ids] of catalog.aliases) for(const i of ids) originals.set(i,signature(id,name));
  function single(tile) {
    const sig=signature(tile.id,tile.name);
    if(!singles.has(sig)) {
      const entry={key:"tile-"+sig,title:tile.name||"Map #"+tile.id,kind:"single",width:1,height:1,
        image:tile.id+".png",tiles:[{...tile,row:1,col:1,turn:0}]};
      singles.set(sig,entry);byKey.set(entry.key,entry);
    }
    return singles.get(sig);
  }
  for(const card of cards) for(const t of card.tiles) single(t);
  const storageKey="wubcraft-gallery/"+catalog.version;
  const selected=new Set();
  try {for(const k of JSON.parse(localStorage.getItem(storageKey)||"[]")) if(byKey.has(k)) selected.add(k);} catch {}
  let mode="all", page=0, filtered=[], result=null, downloading=false;
  const pageSize=48, number=n=>n.toLocaleString();
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const thumb=(file)=>{const img=node("img");img.alt="";img.loading="lazy";img.src="collection/images/"+file;return img;};
  const feature=cards.find(c=>c.title==="Starry Night Bassline")||cards.find(c=>c.kind==="art")||cards[0];
  if(feature) {
    $("feature").hidden=false;$("heroImage").src="collection/images/"+feature.image;
    $("caption").textContent=feature.title+" ↗";$("feature").onclick=()=>detail(feature);
  }
  function toggle(key,checked) {
    checked?selected.add(key):selected.delete(key);
    try {localStorage.setItem(storageKey,JSON.stringify([...selected]));} catch {}
    sync();
  }
  function sync() {
    $("count").textContent=number(selected.size);
    document.querySelectorAll("input[data-pick]").forEach(input=>input.checked=selected.has(input.dataset.pick));
  }
  function check(card,label,cls) {
    const wrap=node("label",null,cls), input=node("input");
    input.type="checkbox";input.dataset.pick=card.key;input.checked=selected.has(card.key);
    input.setAttribute("aria-label","Select "+card.title);
    input.onchange=()=>toggle(card.key,input.checked);
    wrap.append(input);if(label)wrap.append(document.createTextNode(label));return wrap;
  }
  const searchText=new Map(cards.map(c=>[c.key,[c.title,c.originalTitle,...(c.groups||[]),...c.tiles.map(t=>t.name)].join(" ").toLocaleLowerCase()]));
  function filter() {
    const q=$("search").value.trim().toLocaleLowerCase(), numeric=/^#?(-?\d+)$/.exec(q);
    const id=numeric?Number(numeric[1]):null, alias=originals.get(id);
    filtered=cards.filter(c=>{
      if(mode!=="all" && mode!==c.kind || $("overlap").checked && c.overlap) return false;
      if(!q || searchText.get(c.key).includes(q)) return true;
      return numeric && c.tiles.some(t=>t.id===id || signature(t.id,t.name)===alias);
    });
    page=0;render();
  }
  function render() {
    const frag=document.createDocumentFragment(), pages=Math.ceil(filtered.length/pageSize);
    for(const c of filtered.slice(page*pageSize,(page+1)*pageSize)) {
      const article=node("article",null,"card"), open=node("button"), mat=node("span",null,"mat");
      mat.append(thumb(c.image));open.onclick=()=>detail(c);
      open.append(mat,node("strong",c.title),node("small",c.kind==="art"?c.width+" × "+c.height+" · "+c.tiles.length+" tiles":"Single tile · #"+c.tiles[0].id));
      article.append(open,check(c,null,"tick"));frag.append(article);
    }
    $("grid").replaceChildren(frag);
    $("results").textContent=number(filtered.length)+" works"+(!filtered.length?" · Try another search or filter.":"");
    $("page").textContent=pages?"Page "+(page+1)+" of "+pages:"";
    $("prev").disabled=!page;$("next").disabled=page+1>=pages;sync();
  }
  function detail(c) {
    const body=$("detailBody"), img=thumb(c.image);img.className="largeArt";img.alt=c.title;
    body.replaceChildren(node("h2",c.title),node("p",c.kind==="art"?c.width+" × "+c.height+" · "+c.tiles.length+" placements · "+(c.width*c.height-c.tiles.length)+" gaps":"Single tile · Map #"+c.tiles[0].id),img,check(c,"Include in selection","detailPick"));
    if(c.kind==="art") {
      const list=node("details"), items=node("div",null,"tileList");list.append(node("summary","Select individual tiles"));
      for(const t of c.tiles) {
        const row=node("div",null,"entry");row.append(thumb(t.id+".png"),check(single(t),"R"+t.row+" C"+t.col+" · "+(t.name||"Map #"+t.id)+" · #"+t.id));
        items.append(row);
      }
      list.append(items);body.append(list);
    }
    $("detail").showModal();
  }
  function picks(){return [...selected].map(k=>byKey.get(k));}
  function command() {$("commandText").value=result?.commands[Number($("box").value)]||"";}
  function pack() {
    result=null;
    const items=picks(), player=$("player").value.trim();
    try {result=Packing.pack(items,player||"PlayerName");}
    catch(e){$("feedback").textContent=e.message;}
    $("totals").textContent=number(items.length)+" selections"+(result?" · "+number(result.placements)+" maps · "+number(result.bundles)+" bundles · "+number(result.commands.length)+" boxes":"");
    $("portable").disabled=!items.length||downloading;
    const available=Boolean(result?.commands.length && player);
    $("boxLabel").hidden=$("commandText").hidden=!available;
    $("commands").disabled=$("copy").disabled=!available;
    $("box").replaceChildren(...(available?result.commands.map((_,i)=>new Option((i+1)+" of "+result.commands.length,String(i))):[]));
    command();
  }
  function cart() {
    const list=$("cartList");list.replaceChildren();
    for(const c of picks()) {
      const row=node("div",null,"entry"), remove=node("button","Remove");
      remove.onclick=()=>{toggle(c.key,false);row.remove();pack();};
      row.append(node("span",c.title),remove);list.append(row);
    }
    $("feedback").textContent="";pack();$("cart").showModal();
  }
  $("openCart").onclick=cart;$("box").onchange=command;
  $("player").oninput=()=>{$("feedback").textContent="";pack();};
  $("copy").onclick=async()=>{try{await navigator.clipboard.writeText($("commandText").value);$("feedback").textContent="Copied to clipboard.";}
    catch{$("commandText").focus();$("commandText").select();$("feedback").textContent="Command selected. Press Ctrl+C or Command+C.";}};
  $("commands").onclick=()=>Downloads.save(new Blob([result.commands.join("\n")+"\n"],{type:"text/plain;charset=utf-8"}),"wubcraft-commands.txt");
  $("portable").onclick=async()=>{
    const items=picks();downloading=true;$("portable").disabled=true;$("feedback").textContent="Preparing world download…";
    try {const blob=await Downloads.portable(items,catalog,(n,total)=>$("feedback").textContent="Packing "+number(n)+" of "+number(total)+" DATs…");
      Downloads.save(blob,"wubcraft-map-selection.zip");$("feedback").textContent="World download ready. Extract the ZIP and open IMPORT.md.";}
    catch(e){$("feedback").textContent=e.message;}
    finally{downloading=false;$("portable").disabled=!selected.size;}
  };
  for(const b of document.querySelectorAll("[data-kind]")) b.onclick=()=>{
    mode=b.dataset.kind;document.querySelectorAll("[data-kind]").forEach(btn=>btn.setAttribute("aria-pressed",String(b===btn)));filter();};
  $("search").oninput=filter;$("overlap").onchange=filter;
  function changePage(n){page+=n;render();$("collection").scrollIntoView();}
  $("prev").onclick=()=>changePage(-1);$("next").onclick=()=>changePage(1);
  for(const b of document.querySelectorAll("[data-close]")) b.onclick=()=>b.closest("dialog").close();
  filter();
})();

